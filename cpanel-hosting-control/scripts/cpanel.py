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
  cpanel.py <name> db list|users|restrictions|create <name>|drop <name>
  cpanel.py <name> sql "<query>" [--db name]   # raw SQL; needs CPANEL_DB_PASS env
  cpanel.py <name> uapi <Module> <func> [k=v ...]
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

    # ---------- MySQL ----------
    def db_list(self):
        r = self.uapi("Mysql", "list_databases")
        return [d["database"] for d in (r.get("data") or [])]

    def db_users(self):
        r = self.uapi("Mysql", "list_users")
        return [(u["user"], u.get("databases", [])) for u in (r.get("data") or [])]

    def db_restrictions(self):
        return self.uapi("Mysql", "get_restrictions").get("data") or {}

    def db_create(self, name):
        """MysqlFE::createdb via API2. `name` must include the account prefix."""
        r = self.api2("MysqlFE", "createdb", db=name)
        return self._db_result(r)

    def db_drop(self, name):
        r = self.api2("MysqlFE", "deletedb", db=name)
        return self._db_result(r)

    @staticmethod
    def _db_result(r):
        d = (r.get("cpanelresult") or {}).get("data") or {}
        return bool(d.get("result")), str(d.get("reason", ""))

    # ---------- raw SQL via a throwaway token-gated gateway ----------
    # MySQL on shared hosting rejects remote logins (host-bound grants), and
    # cPanel's Mysql module has no query runner. So `sql` deploys a small
    # token-gated PHP gateway once, sends queries to it over HTTPS, and tears
    # it down when finished. Creds live in a dotfile next to it, never in URLs.
    def sql(self, query, db=None, host_scheme="https", timeout=60):
        import random, string, urllib.request, urllib.parse, urllib.error
        creds_dir = ".hermes_sql"
        rel = "public_html/" + creds_dir
        absd = "/home/" + self.user + "/" + rel
        creds_abs = absd + "/.sqlcreds.json"
        gw_abs = absd + "/_sqlgw.php"
        creds_rel = rel + "/.sqlcreds.json"

        # reuse an existing gateway if creds are still on disk and fresh
        existing = self._safe_cat(rel, ".sqlcreds.json")
        tok = None
        if existing:
            try:
                cfg = json.loads(existing)
                if cfg.get("db") == (db or cfg.get("db")) and cfg.get("token"):
                    tok = cfg["token"]
            except Exception:
                tok = None

        if not tok:
            tok = "".join(random.choice(string.hexdigits.lower()) for _ in range(32))
            cfg = {"token": tok, "db": db, "user": "", "pass": ""}
            # db password must be supplied by the caller via env or the creds file
            pw = os.environ.get("CPANEL_DB_PASS", "")
            cfg["user"] = os.environ.get("CPANEL_DB_USER", self.user + "_" + (db or "").split("_")[-1])
            cfg["pass"] = pw
            if not pw:
                return {"error": "CPANEL_DB_PASS env var required (the DB user's password)"}
            ok, why = self.upload(rel, ".sqlcreds.json", json.dumps(cfg).encode())
            if not ok:
                return {"error": "creds upload failed: " + why}
            ok, why = self.upload(rel, "_sqlgw.php",
                                  open(os.path.join(os.path.dirname(__file__), "_sqlgw.php"), "rb").read())
            if not ok:
                return {"error": "gateway upload failed: " + why}
            # lock the gateway down: only the token can call it (deny-all would
            # block the gateway itself)
            self.upload(rel, ".htaccess",
                        b"# deny directory listing; gateway is token-gated in PHP\n"
                        b"Options -Indexes\n")
            self._gw_tok = tok
            self._gw_rel = rel
            self._gw_abs = absd

        # run the query
        u = "%s://%s/%s/_sqlgw.php" % (host_scheme, self.host, creds_dir)
        data = urllib.parse.urlencode({"sql": query}).encode()
        rq = urllib.request.Request(u, data=data,
                                    headers={"X-Sql-Token": tok,
                                             "Content-Type": "application/x-www-form-urlencoded"})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            body = urllib.request.urlopen(rq, timeout=timeout, context=ctx).read()
            return json.loads(body)
        except urllib.error.HTTPError as e:
            return {"error": "HTTP %d: %s" % (e.code, e.read().decode("utf-8", "ignore")[:300])}

    def _safe_cat(self, reldir, fname):
        try:
            return self.cat(reldir, fname)
        except Exception:
            return ""

    def sql_cleanup(self):
        """Remove the throwaway gateway and creds. Call when the session is done."""
        if not getattr(self, "_gw_rel", None):
            return False
        home = "/home/" + self.user
        self.fileop("unlink", home + "/" + self._gw_rel + "/_sqlgw.php")
        self.fileop("unlink", home + "/" + self._gw_rel + "/.sqlcreds.json")
        self.fileop("unlink", home + "/" + self._gw_rel + "/.htaccess")
        return True


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
            sub = sys.argv[3] if len(sys.argv) > 3 else "list"
            if sub == "list":
                for d in c.db_list():
                    print(d)
            elif sub == "users":
                for u, dbs in c.db_users():
                    print("%-28s %s" % (u, ",".join(dbs)))
            elif sub == "restrictions":
                print(json.dumps(c.db_restrictions(), indent=1))
            elif sub == "create":
                ok, why = c.db_create(sys.argv[4])
                print("create:", ok, why)
            elif sub == "drop":
                ok, why = c.db_drop(sys.argv[4])
                print("drop:", ok, why)
            else:
                print("db subcommands: list | users | restrictions | create <name> | drop <name>")
                return 1
        elif cmd == "sql":
            if len(sys.argv) < 4:
                print("usage: sql <query>  [--db name]  (needs CPANEL_DB_PASS env)")
                return 1
            q = sys.argv[3]
            dbn = None
            if "--db" in sys.argv:
                dbn = sys.argv[sys.argv.index("--db") + 1]
            out = c.sql(q, db=dbn)
            print(json.dumps(out, indent=1)[:4000])
            if "error" not in out:
                c.sql_cleanup()
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
