#!/usr/bin/env python3
"""Serve the console and the case room on localhost.

    python -m web.serve            # http://localhost:4000
    python -m web.serve --port 8080
    python -m web.serve --no-build # serve what is already in artifacts/

Standard library only, no framework. Rebuilds the pages first unless told not
to, then serves artifacts/ with caching off, so a rebuild in another terminal
shows up on refresh.

    /            the console
    /case        the case room
    /artifacts/  everything else that got generated
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import socket
import socketserver
import threading
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS = os.path.join(ROOT, "artifacts")
# the two built pages. artifacts/ holds generated data, never HTML.
DEMO = os.path.join(ROOT, "demo")
DEFAULT_PORT = 4000

ROUTES = {
    "/": "console.html",
    "/console": "console.html",
    "/case": "case_room.html",
    "/case-room": "case_room.html",
}


class Handler(http.server.SimpleHTTPRequestHandler):
    # The default is HTTP/1.0, which closes after every response. A page this
    # size plus its images then opens a connection per asset, and clients that
    # assume keep-alive stall waiting for more. Content-Length is always set
    # here, so 1.1 is safe.
    # HTTP/1.1 so a browser reuses one connection for the page and its images.
    # Measured on this machine: 60 keep-alive requests all served, worst 49 ms.
    # The same 60 as separate connect-and-close cycles is much slower and drops
    # some, which is Windows socket churn rather than anything here, and is not
    # what a browser does. Content-Length is always set, so 1.1 is safe.
    protocol_version = "HTTP/1.1"
    # An idle keep-alive connection otherwise pins a thread forever.
    timeout = 8

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (TimeoutError, OSError):
            self.close_connection = True

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DEMO, **kw)

    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
        if clean in ROUTES:
            return os.path.join(DEMO, ROUTES[clean])
        # demo/ is what gets served, so /artifacts/ has to be redirected at the
        # other directory by hand. Segments are filtered rather than joined
        # blind: this is a local tool, but "serve any path the client asks for"
        # is not a thing to write down even once.
        if clean == "/artifacts" or clean.startswith("/artifacts/"):
            rel = clean[len("/artifacts"):].strip("/")
            parts = [p for p in rel.split("/")
                     if p and p not in (".", "..") and "\\" not in p]
            return os.path.join(ARTIFACTS, *parts)
        return super().translate_path(path)

    def end_headers(self):
        # a rebuild in another terminal should show up on refresh, not after a
        # hard reload nobody thinks to do
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Quiet on success, loud on anything else. Guarded because log_error
        # calls through here with a different argument shape, and an
        # IndexError raised inside a handler thread kills the response.
        try:
            if len(args) > 1 and str(args[1]).startswith(("4", "5")):
                print(f"  {args[0]} -> {args[1]}")
        except Exception:
            pass


def build_pages():
    from . import dashboard
    have_bundle = os.path.exists(os.path.join(ARTIFACTS, "bundle.json"))
    print("building the console")
    try:
        dashboard.build(out=os.path.join("demo", "console.html"))
    except Exception as e:
        # Re-exporting the model needs LightGBM. If that is unavailable, still
        # rebuild the page around the bundle already on disk rather than
        # refusing to serve anything.
        if not have_bundle:
            raise
        print(f"  could not re-export the model ({type(e).__name__}), "
              f"rebuilding the page from the existing bundle")
        dashboard.build(out=os.path.join("demo", "console.html"),
                        rebuild=False)
    if not os.path.exists(os.path.join(DEMO, "case_room.html")):
        print("building the case room")
        from . import case_room
        case_room.build()


def port_is_taken(port: int) -> bool:
    """Is something already answering on this port?

    allow_reuse_address is set below, and on Windows that behaves like
    SO_REUSEPORT: a second process binds the same port happily, and the two
    then race to accept. Half the requests come from the stale server, so the
    page half-updates and the obvious conclusion, that the new code is broken,
    is wrong. bind() will not tell us, so ask by connecting.
    """
    for fam, addr in ((socket.AF_INET, ("127.0.0.1", port)),
                      (socket.AF_INET6, ("::1", port))):
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.4)
                if probe.connect_ex(addr) == 0:
                    return True
        except OSError:
            pass
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"default {DEFAULT_PORT}")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-build", action="store_true",
                    help="serve what is already in artifacts/")
    ap.add_argument("--no-open", action="store_true",
                    help="do not open a browser")
    a = ap.parse_args(argv)

    os.chdir(ROOT)
    if not a.no_build:
        build_pages()
    else:
        need = ROUTES["/"]
        if not os.path.exists(os.path.join(ARTIFACTS, need)):
            raise SystemExit(f"artifacts/{need} does not exist. "
                             "Run without --no-build, or `python run.py console`.")

    # Threading, not the single-threaded default: one slow or half-open
    # connection was enough to make the next request hang.
    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    class Server6(Server):
        """Dual-stack. On Windows `localhost` resolves to ::1 before 127.0.0.1,
        so a v4-only bind makes every request to http://localhost hang for
        seconds and some of them time out outright. An IPv6 socket with
        V6ONLY off answers on both."""
        address_family = socket.AF_INET6

        def server_bind(self):
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
            super().server_bind()

    def listen():
        if a.host in ("127.0.0.1", "localhost"):
            try:
                return Server6(("::", a.port), Handler)
            except OSError:
                pass          # no usable IPv6, fall through to v4
        return Server((a.host, a.port), Handler)

    if port_is_taken(a.port):
        raise SystemExit(
            f"something is already serving on port {a.port}, most likely an "
            f"older copy of this server left running. Two of them can hold the "
            f"same port here and they take turns answering, so you would get a "
            f"mix of the old pages and the new ones. Stop it first, or use "
            f"--port {a.port + 1}.")

    try:
        httpd = listen()
    except OSError as e:
        raise SystemExit(
            f"could not bind {a.host}:{a.port} ({e}). Something else is on that "
            f"port; try --port {a.port + 1}.")

    url = f"http://localhost:{a.port}/"
    print(f"\n  console    {url}")
    print(f"  case room  {url}case")
    print(f"  artifacts  {url}artifacts/")
    print("\n  ctrl-c to stop\n")
    if not a.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
