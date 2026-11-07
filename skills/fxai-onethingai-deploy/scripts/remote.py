#!/usr/bin/env python
"""往仙宫云实例跑一次性检查/维护脚本。

用法：python remote.py <本地脚本.sh>
脚本内容原样在远端 bash 执行，输出原样回显。
"""
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from otai_ssh import SSH  # noqa: E402
from otai_cfg import ssh_cred  # noqa: E402


def get_ssh(profile="default"):
    sec = ssh_cred()
    return SSH(sec["host"], sec["port"], sec["user"],
               password=sec.get("password") or None, key=sec.get("key") or None)


def run_local_script(path, timeout=600, bg=False):
    ssh = get_ssh()
    try:
        body = open(path, encoding="utf-8").read()
        remote = "/tmp/otai_task.sh"
        ssh.put_text(body, remote)
        ssh.run(f"chmod +x {shlex.quote(remote)}")
        if bg:
            ssh.run_bg(remote, "/tmp/otai_task.log", pid_path="/tmp/otai_deploy.pid",
                       rc_path="/tmp/otai_task.rc")
            time.sleep(2)
            return ssh.stream("/tmp/otai_task.log", poll=5.0, tail_from=0)
        r = ssh.run(f"bash {shlex.quote(remote)}", timeout=timeout)
        sys.stdout.write(r.out)
        if r.err:
            sys.stderr.write(r.err)
        return r.rc
    finally:
        ssh.close()


import time  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(run_local_script(sys.argv[1],
                              timeout=int(sys.argv[2]) if len(sys.argv) > 2 else 600,
                              bg="--bg" in sys.argv))
