import ast, io, json, os, sys, tarfile, tempfile

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "push")
sys.path.insert(0, HERE)
for f in ("gen_config.py", "interview.py", "profile_tools.py"):
    ast.parse(open(os.path.join(HERE, f), encoding="utf-8").read())
print("syntax OK: gen_config, interview, profile_tools")

import interview as iv
import profile_tools as pt

keys = ["phone", "dev", "audio", "media", "claude", "apps"]
v = iv.make_v_extras(keys)
cases = {"": (True, ""), "none": (True, ""), "all": (True, ",".join(keys)), "phone, dev": (True, "phone,dev"),
         "phone dev phone": (True, "phone,dev"), "erase disk": (False, None), "phone;ls": (False, None)}
for k, exp in cases.items():
    got = v(k)
    assert got[0] == exp[0] and (exp[1] is None or got[1] == exp[1]), (k, got)
print("extras validator OK")

assert iv.v_username("root")[0] is False
assert iv.v_username("dana; id")[0] is False
assert iv.v_username("dana_2")[0] is True
assert iv.v_hostname("Audio Box")[0] is False
assert iv.v_hostname("audiobox")[0] is True
print("username/hostname validators OK")


def mk(entries):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w") as t:
        for name, data, kind in entries:
            ti = tarfile.TarInfo(name)
            if kind == "file":
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
            else:
                ti.type = tarfile.SYMTYPE
                ti.linkname = "/etc/passwd"
                t.addfile(ti)
    return b.getvalue()


good = mk([("PROFILE-V1", b"x", "file"),
           ("manifest.json", json.dumps({"defaults": {"timezone": "America/Los_Angeles"}}).encode(), "file"),
           ("claude/settings.json", b"{}", "file"), ("claude/memory/MEMORY.md", b"index", "file")])
assert pt.read_manifest(good) == {"timezone": "America/Los_Angeles"}
root = tempfile.mkdtemp()
written = pt.restore(good, root, "dana")
assert len(written) == 2, written
print("good archive restores exactly 2 allowed files:", [x.replace(os.sep, "/") for x in written])

evil = mk([("PROFILE-V1", b"x", "file"), ("../../etc/passwd", b"pwned", "file"), ("/etc/shadow", b"pwned", "file"),
           ("claude/memory/../../../evil.md", b"pwned", "file"), ("claude/memory/not-md.sh", b"pwned", "file"),
           ("claude/.credentials.json", b"tok", "file"), ("claude/history.jsonl", b"chat", "file"), ("claude/link", b"", "symlink")])
root2 = tempfile.mkdtemp()
assert pt.restore(evil, root2, "dana") == []
found = [os.path.join(dp, f) for dp, _, fs in os.walk(root2) for f in fs]
assert found == [], found
winstyle = mk([("./PROFILE-V1", b"x", "file"), ("./manifest.json", json.dumps({"defaults": {"kb": "us"}}).encode(), "file"),
               ("./claude/settings.json", b"{}", "file"), ("./claude/memory/MEMORY.md", b"index", "file")])
assert pt.has_sentinel(winstyle) and pt.has_sentinel(good)
assert not pt.has_sentinel(mk([("claude/memory/MEMORY.md", b"x", "file")]))
assert pt.read_manifest(winstyle) == {"kb": "us"}
root3 = tempfile.mkdtemp()
assert len(pt.restore(winstyle, root3, "dana")) == 2
dotted = mk([("PROFILE-V1", b"x", "file"), ("../claude/settings.json", b"pwned", "file"), (".../claude/settings.json", b"pwned", "file")])
assert pt.restore(dotted, tempfile.mkdtemp(), "dana") == []
print("Windows-style './' names work; look-alike traversal names are ignored")
print("hostile archive: nothing written (traversal, absolute path, symlink, wrong file types, token and history files all ignored)")

try:
    pt.restore(good, root, "root; id")
    raise SystemExit("bad username was accepted")
except ValueError:
    print("bad username refused")

big = mk([("PROFILE-V1", b"x", "file"), ("claude/memory/a.md", b"x" * (6 * 1024 * 1024), "file")])
try:
    pt.restore(big, tempfile.mkdtemp(), "dana")
    raise SystemExit("oversized archive was accepted")
except ValueError:
    print("oversized archive refused")

import re, subprocess
from pathlib import Path
tree = ast.parse(open(os.path.join(HERE, "gen_config.py"), encoding="utf-8").read())
ns = {"re": re, "subprocess": subprocess, "Path": Path}
for node in tree.body:
    if (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "GFX" for t in node.targets)) or (isinstance(node, ast.FunctionDef) and node.name == "detect_gpu"):
        exec(compile(ast.Module([node], []), "gen_config", "exec"), ns)


class Fake:
    def __init__(self, text): self.text = text
    def check_output(self, *a, **k): return self.text


def detect(text):
    ns["subprocess"] = Fake(text)
    return ns["detect_gpu"]()[0]


G = ns["GFX"]
gtx1660 = '01:00.0 "VGA compatible controller [0300]" "NVIDIA Corporation [10de]" "TU116 [GeForce GTX 1660] [2184]" -ra1 "ZOTAC [19da]" "Device [2504]"'
gtx1080 = '01:00.0 "VGA compatible controller [0300]" "NVIDIA Corporation [10de]" "GP104 [GeForce GTX 1080] [1b80]" -ra1 "ASUS [1043]" "Device [8591]"'
rtx3080 = '01:00.0 "VGA compatible controller [0300]" "NVIDIA Corporation [10de]" "GA102 [GeForce RTX 3080] [2206]" -ra1 "Gigabyte [1458]" "Device [403f]"'
rtx3050m = '01:00.0 "3D controller [0302]" "NVIDIA Corporation [10de]" "GA107M [GeForce RTX 3050 Mobile] [25a2]" -ra1 "Dell [1028]" "Device [0a5c]"'
amd = '03:00.0 "VGA compatible controller [0300]" "Advanced Micro Devices, Inc. [AMD/ATI] [1002]" "Navi 22 [Radeon RX 6700 XT] [73df]" -rc1 "Sapphire [1da2]" "Device [e445]"'
intel = '00:02.0 "VGA compatible controller [0300]" "Intel Corporation [8086]" "UHD Graphics 630 [3e92]" "Dell [1028]" "Device [087c]"'
tricky = '03:00.0 "VGA compatible controller [0300]" "Advanced Micro Devices, Inc. [AMD/ATI] [1002]" "Weird card [10de]" "X [1234]" "Device [0001]"'
assert detect(gtx1660) == G["nvidia_open"], detect(gtx1660)
assert detect(gtx1080) == G["nouveau"]
assert detect(rtx3080) == G["nvidia_open"]
assert detect(rtx3050m) == G["nvidia_open"]
assert detect(amd) == G["amd"]
assert detect(intel) == G["intel"]
assert detect(tricky) == G["amd"], "vendor is read from the vendor field, not any 10de-looking text"
assert detect(intel + "\n" + rtx3050m) == G["all"]
assert detect("") == G["all"]
print("graphics detection OK (GTX 1660 TU116 -> NVIDIA open module; Pascal -> nouveau; hybrid/unknown -> safe default)")

print("ALL HOST TESTS PASSED")
