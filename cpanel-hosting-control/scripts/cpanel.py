#!/usr/bin/env python3
"""cpanel.py — cPanel shared-hosting control from the terminal.

No FTP, no scp, no browser. Session-token UAPI + legacy API2 file ops.

Credentials:  ~/.hermes/secrets/cpanel_<name>   one line:  user|password|host
(chmod 600; the script never prints the password.)

Usage:
  cpanel.py <name> login                    # verify auth
  cpanel.py <name> list <dir>               # dir is HOME-relative, e.g. public_html/api
  cpanel.py <name> tree <dir> [depth]
  cpanel.py <name> put <localfile> <dir>    # safe overwrite: .new -> .bak -> swap, rollback on fail
  cpanel.py <name> cat <remotefile> <out>   # download a file's content to <out> ("-" for stdout)
  cpanel.py <name> move <src> <dst>         # ABSOLUTE paths under /home/<user>
  cpanel.py <name> rm <path>                # unlink (absolute). DANGEROUS: no trash.
  cpanel.py <name> uapi <Module> <func> [k=v ...]
  cpanel.py <name> db list                  # MySQL databases/users
  cpanel.py <name> verify <url>             # fetch live URL, print body marker check
"""
import sys, os, json, ssl, urllib.request, urllib.parse, http.cookiejar

SECRETS = os.path.expanduser("~/.hermes/secrets")


def load(name):
    p = os.path.join(SECRETS, "cpanel_" + name)
    user, pw, host = open(p, encoding="utf-8").read().strip().split("|", 2)
    return user, pw, host


class CPanel:
    def __init__(self, name):
        user, pw, host = load(name)
        self.user, self.host, self.pw = user, host, pw
        self.base = "https://%s:2083" % host
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=self.ctx))
        self.token = None

    # ---------- login ----------
    def login(self):
        if self.token:
            return
        # The form field is `pass`, NOT `password` — `password` returns 401.
        data = urllib.parse.urlencode({"user": self.user, "pass": str(self.pw)}).encode()
        r = json.load(self.op.open(self.base + "/login/?login_only=1", data, timeout=40))
        tok = r.get("security_token") or r.get("cpanel_security_token")
        if not tok:
            raise RuntimeError("login failed: %s" % json.dumps(r)[:200])
        self.token = str(tok)
        if not self.token.startswith("/"):
            self.token = "/" + self.token

    def _url(self, path):
        self.login()
        assert self.token is not None
        return self.base + self.token + path

    # ---------- UAPI (GET; NO .json suffix or cPanel 114 says "Illegal function name") ----------
    def uapi(self, module, func, **kw):
        u = self._url("/execute/%s/%s" % (module, func))
        if kw:
            u += "?" + urllib.parse.urlencode(kw)
        return json.load(self.op.open(u, timeout=90))

    # ---------- API2 (POST; needed for fileop move/unlink) ----------
    def api2(self, module, func, **kw):
        d = {"cpanel_jsonapi_user": self.user, "cpanel_jsonapi_apiversion": "2",
             "cpanel_jsonapi_module": module, "cpanel_jsonapi_func": func}
        d.update(kw)
        rq = urllib.request.Request(self._url("/json-api/cpanel"),
                                    data=urllib.parse.urlencode(d).encode())
        return json.load(self.op.open(rq, timeout=90))

    # ---------- file helpers ----------
    def listdir(self, rel):
        r = self.uapi("Fileman", "list_files", dir=rel)
        items = r.get("data") or []
        # list_files returns a LIST at top level on some hosts, 'data' on others
        if isinstance(items, dict):
            items = items.get("files") or []
        return [(i.get("file"), i.get("type"), i.get("size", 0)) for i in items]

    def upload(self, reldir, fname, content):
        """Upload a NEW file (must not already exist; caller renames first)."""
        b = "----hermesup%d" % (os.getpid() * 7 % 100000)
        parts = [
            ("--%s\r\nContent-Disposition: form-data; name=\"dir\"\r\n\r\n%s\r\n" % (b, reldir)).encode(),
            ("--%s\r\nContent-Disposition: form-data; name=\"file-1\"; filename=\"%s\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n" % (b, fname)).encode(),
            content,
            ("\r\n--%s--\r\n" % b).encode(),
        ]
        rq = urllib.request.Request(self._url("/execute/Fileman/upload_files"),
                                    data=b"".join(parts))
        rq.add_header("Content-Type", "multipart/form-data; boundary=" + b)
        r = json.load(self.op.open(rq, timeout=120))
        ups = (r.get("data") or {}).get("uploads") or [{}]
        return ups[0].get("status") == 1, ups[0].get("reason", "")

    def fileop(self, op, src, dst=None):
        # NOTE: `sourcefiles` plural, and ABSOLUTE paths for fileop.
        kw = {"op": op, "sourcefiles": src}
        if dst:
            kw["destfiles"] = dst
        r = self.api2("Fileman", "fileop", **kw)
        d = (r.get("cpanelresult") or {}).get("data") or [{}]
        return d[0].get("result") == 1, json.dumps(r)[:200]

    def put(self, local, reldir):
        """Safe deploy: upload .hermesnew -> backup current to .bak_ -> swap in.
        Rolls back if the final move fails. Backup kept at .bak_<name>."""
        fname = os.path.basename(local)
        content = open(local, "rb").read()
        home = "/home/" + self.user
        target = reldir + "/" + fname
        bak = reldir + "/.bak_" + fname
        tmp = reldir + "/" + fname + ".hermesnew"
        ok, why = self.upload(reldir, fname + ".hermesnew", content)
        if not ok:
            raise RuntimeError("upload failed: " + why)
        self.fileop("unlink", home + "/" + bak)
        self.fileop("move", home + "/" + target, home + "/" + bak)
        ok2, why2 = self.fileop("move", home + "/" + tmp, home + "/" + target)
        if not ok2:
            self.fileop("move", home + "/" + bak, home + "/" + target)
            raise RuntimeError("install failed: " + why2)
        return True

    def cat(self, reldir, fname):
        """Read a remote file's content via Fileman get_file_content (UAPI)."""
        r = self.uapi("Fileman", "get_file_content", dir=reldir, file=fname)
        data = r.get("data") or {}
        return str(data.get("content", ""))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    name, cmd = sys.argv[1], sys.argv[2]
    c = CPanel(name)
    try:
        if cmd == "login":
            c.login()
            print("OK token=%s... host=%s user=%s" % (c.token[:12], c.host, c.user))
        elif cmd == "list":
            for f, t, s in c.listdir(sys.argv[3]):
                print("%-40s %s %s" % (f, t, s))
        elif cmd == "put":
            print("deployed:", c.put(sys.argv[3], sys.argv[4]))
        elif cmd == "cat":
            out = c.cat(os.path.dirname(sys.argv[3]) or ".", os.path.basename(sys.argv[3]))
            dest = sys.argv[4] if len(sys.argv) > 4 else "-"
            if dest == "-":
                sys.stdout.write(out)
            else:
                open(dest, "w").write(out)
                print("saved %d bytes -> %s" % (len(out), dest))
        elif cmd == "uapi":
            kw = dict(a.split("=", 1) for a in sys.argv[5:])
            print(json.dumps(c.uapi(sys.argv[3], sys.argv[4], **kw), indent=1)[:4000])
        elif cmd == "db":
            print(json.dumps(c.uapi("Mysql", "list_databases"), indent=1)[:2000])
        elif cmd == "move":
            print(c.fileop("move", sys.argv[3], sys.argv[4]))
        elif cmd == "rm":
            print(c.fileop("unlink", sys.argv[3]))
        else:
            print("unknown cmd:", cmd)
            return 1
    except Exception as e:
        print("ERROR:", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
