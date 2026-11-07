"""SSH 传输层：优先 paramiko（支持密码），未安装时回退系统 ssh（密钥认证）。

对外只暴露 SSH 类，paramiko / subprocess 的调用都收在这一层里。
"""
from __future__ import annotations

import base64
import getpass
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass


class SSHFail(RuntimeError):
    pass


@dataclass
class RunResult:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def text(self) -> str:
        return (self.out or "") + (("\n[stderr]\n" + self.err) if self.err else "")


def have_paramiko() -> bool:
    try:
        import paramiko  # noqa: F401
        return True
    except Exception:
        return False


class SSH:
    def __init__(self, host, port=22, user="root", password=None, key=None, timeout=25):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.key = key
        self.timeout = timeout
        self._client = None
        self._backend = None

    # ---------- 连接 ----------
    def connect(self):
        if self._client is not None or self._backend == "ssh":
            return self
        if have_paramiko():
            import paramiko

            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kw = dict(hostname=self.host, port=self.port, username=self.user, timeout=self.timeout)
            if self.password:
                kw["password"] = self.password
                kw["look_for_keys"] = False
                kw["allow_agent"] = False
            if self.key:
                kw["key_filename"] = self.key
            try:
                c.connect(**kw)
            except Exception as e:
                raise SSHFail(
                    f"paramiko 连接 {self.user}@{self.host}:{self.port} 失败：{e}\n"
                    f"排查：① 用户名/端口对不对（仙宫云实例详情页有 SSH 命令）② 密码对不对 "
                    f"③ 本机私钥 ACL 是否被重置（Git Bash 常见 UNPROTECTED PRIVATE KEY 报错）"
                ) from e
            self._client = c
            self._backend = "paramiko"
        else:
            if self.password:
                raise SSHFail(
                    "需要密码登录，但本机没有 paramiko。二选一：\n"
                    "  A) pip install paramiko（推荐）\n"
                    "  B) 改用密钥认证：--key ~/.ssh/id_ed25519（且本机已装公钥）"
                )
            self._backend = "ssh"
        return self

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *a):
        self.close()

    # ---------- 执行 ----------
    def run(self, cmd: str, timeout: int | None = None, check: bool = False) -> RunResult:
        self.connect()
        if self._backend == "paramiko":
            return self._run_paramiko(cmd, timeout, check)
        return self._run_ssh(cmd, timeout, check)

    def _run_paramiko(self, cmd, timeout, check) -> RunResult:
        _, sout, serr = self._client.exec_command(cmd, timeout=timeout, get_pty=False)
        out = sout.read().decode("utf-8", "replace")
        err = serr.read().decode("utf-8", "replace")
        rc = sout.channel.recv_exit_status()
        r = RunResult(rc, out, err)
        if check and rc != 0:
            raise SSHFail(f"远端命令失败 rc={rc}\n$ {cmd}\n{r.text()}")
        return r

    def _run_ssh(self, cmd, timeout, check) -> RunResult:
        argv = [
            "ssh", "-p", str(self.port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", "ServerAliveInterval=30",
        ]
        if self.key:
            argv += ["-i", self.key]
        argv += [f"{self.user}@{self.host}", cmd]
        try:
            p = subprocess.run(argv, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise SSHFail(f"SSH 执行超时（{timeout}s）：{cmd[:120]}...") from e
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        r = RunResult(p.returncode, out, err)
        if check and r.rc != 0:
            raise SSHFail(f"远端命令失败 rc={r.rc}\n$ {cmd}\n{r.text()}")
        return r

    def put_text(self, text: str, remote_path: str):
        """把一段文本写到远端文件（base64 传输，绕开所有引号/换行地狱）。"""
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        self.run(f"mkdir -p $(dirname {shlex.quote(remote_path)})")
        # 分块写，单行长度受控
        self.run(f"rm -f {shlex.quote(remote_path)}")
        chunk = 60000
        for i in range(0, len(b64), chunk):
            part = b64[i:i + chunk]
            op = ">" if i == 0 else ">>"
            self.run(f"printf '%s' '{part}' {op} {shlex.quote(remote_path)}.b64")
        self.run(f"base64 -d {shlex.quote(remote_path)}.b64 > {shlex.quote(remote_path)} && rm -f {shlex.quote(remote_path)}.b64")

    def put_bytes(self, data: bytes, remote_path: str):
        """二进制安全上传（tar.gz / 模型权重等）。

        复用 put_text 的 base64 分块思路，只是入参是 bytes 而不是 str。
        4.5MB 的 tar.gz 编码后 6MB，分 60KB 一块，约 100 次 run，十几秒能传完。
        """
        b64 = base64.b64encode(data).decode("ascii")
        self.run(f"mkdir -p $(dirname {shlex.quote(remote_path)})")
        self.run(f"rm -f {shlex.quote(remote_path)}")
        chunk = 60000
        for i in range(0, len(b64), chunk):
            part = b64[i:i + chunk]
            op = ">" if i == 0 else ">>"
            self.run(f"printf '%s' '{part}' {op} {shlex.quote(remote_path)}.b64", timeout=180)
        self.run(f"base64 -d {shlex.quote(remote_path)}.b64 > {shlex.quote(remote_path)} "
                 f"&& rm -f {shlex.quote(remote_path)}.b64", timeout=300)

    def run_bg(self, script_path: str, log_path: str, pid_path: str = "/tmp/otai_deploy.pid",
               rc_path: str = "/tmp/otai_deploy.rc", timeout: int = 40):
        """把脚本丢到远端后台跑。

        必须用 setsid + `</dev/null` + 全部流重定向：否则后台子进程会一直攥着
        paramiko channel 的 pipe，exec_command 永远等不到 EOF，直接 PipeTimeout。
        """
        cmd = (f"cd /tmp; rm -f {shlex.quote(log_path)} {shlex.quote(rc_path)}; "
               f"setsid bash {shlex.quote(script_path)} "
               f"</dev/null >{shlex.quote(log_path)} 2>&1 & "
               f"echo $! > {shlex.quote(pid_path)}; echo LAUNCHED")
        r = self.run(cmd, timeout=timeout)
        if "LAUNCHED" not in (r.out or ""):
            raise SSHFail(f"后台启动失败：{r.text()}")
        return r

    def stream(self, log_path: str, poll: float = 3.0, tail_from: int = 0, on_line=None):
        """把远端日志文件从 tail_from 字节开始增量拉回来打印。"""
        if on_line is None:
            def on_line(s):
                sys.stdout.write(s)
                sys.stdout.flush()
        buf = ""
        while True:
            r = self.run(f"tail -c +{tail_from + 1} {shlex.quote(log_path)} 2>/dev/null")
            new = r.out or ""
            if not new.startswith(buf):
                for line in new[len(buf):].splitlines(True):
                    on_line(line)
                buf = new
            if self._bg_done():
                r2 = self.run(f"tail -c +{tail_from + 1} {shlex.quote(log_path)} 2>/dev/null")
                for line in (r2.out or "")[len(buf):].splitlines(True):
                    on_line(line)
                return self._bg_rc()
            time.sleep(poll)

    def _bg_pidfile(self):
        return "/tmp/otai_deploy.pid"

    def _bg_done(self) -> bool:
        r = self.run(f"test -f {self._bg_pidfile()} && ! kill -0 $(cat {self._bg_pidfile()}) 2>/dev/null && echo DONE || true")
        return "DONE" in (r.out or "")

    def _bg_rc(self) -> int:
        r = self.run(f"cat /tmp/otai_deploy.rc 2>/dev/null || echo 0")
        try:
            return int((r.out or "0").strip().splitlines()[-1])
        except Exception:
            return 0


def ask_password(host, port, user):
    return getpass.getpass(f"{user}@{host}:{port} 的密码（直接回车 = 不用密码，走密钥）: ")
