from pathlib import Path
from queue import Queue
import io
import os
import subprocess
import sys
import threading

SENTINEL = object()


class Streamer:

    def __init__(self, stream=None):
        self.in_stream = Queue() if stream is None else stream

    def reader(self, in_stream):
        # handle queues
        if isinstance(in_stream, Queue):
            while True:
                chunk = in_stream.get()
                if chunk == SENTINEL:
                    return
                yield chunk

        # handles buffers
        try:
            for chunk in iter(lambda: self.in_stream.readline(2048), ""):
                if not chunk:
                    return
                yield chunk
        except ValueError:
            # read raises a ValueError on closed stream
            pass

    def writer(self, generator, out_stream):
        if isinstance(out_stream, Queue):
            # handle queues
            for chunk in generator:
                out_stream.put(chunk)
            out_stream.put(SENTINEL)
        else:
            # handle buffers
            for chunk in generator:
                if getattr(out_stream, 'mode', None) == 'w':
                    out_stream.buffer.write(chunk)
                else:
                    out_stream.write(chunk)
            try:
                out_stream.flush()
                if not isinstance(out_stream, io.BytesIO):
                    out_stream.close()
            except ValueError:
                # read raises a ValueError on closed stream
                pass


    def _plug(self, out_stream):
        if isinstance(out_stream, Streamer):
            out_stream = out_stream.in_stream
        self.writer(self.reader(self.in_stream), out_stream)

    def plug(self, out_stream):
        t = threading.Thread(target=self._plug, args=(out_stream,))
        t.start()
        return t


class Cmd:

    def __init__(self, cmd, *args):
        # Resolve full path
        cmd_path = None
        for p in os.environ.get('PATH', '').split(os.pathsep):
            if cmd in os.listdir(p):
                cmd_path = Path(p) / cmd
                break
        else:
            raise Exception(f'Command not found: {cmd}')
        self.cmd_path = cmd_path
        self.args = args
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def setup(self, stdin=None, stdout=None, stderr=None):
        if not self.stdin:
            self.stdin = stdin
        if not self.stdout:
            self.stdout = stdout
        if not self.stderr:
            self.stderr = stderr

    def __call__(self, *extra_args):
        out_buff = io.BytesIO()
        out_stream = Streamer()
        out_stream.plug(out_buff)
        self.setup(stdout=out_stream)
        self.run(*extra_args)
        return out_buff.getvalue().decode()

    def run(self, *extra_args):
        process = subprocess.Popen(
            (self.cmd_path,) + self.args + extra_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        )

        # Plug io
        Streamer(process.stdout).plug(self.stdout or sys.stdout)
        Streamer(process.stderr).plug(self.stderr or sys.stderr)
        if self.stdin:
            if isinstance(self.stdin, Streamer):
                self.stdin.plug(process.stdin)
            else:
                Streamer(self.stdin).plug(process.stdin)

        errcode = process.wait()
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        return errcode

    def pipe(self, cmd, *args):
        # Chain IO
        other = Cmd(cmd, *args)
        out_stream = Streamer()
        self.setup(stdout=out_stream)
        threading.Thread(target=self.run).start()
        other.setup(stdin=out_stream)
        return other


class SH:

    def __getattr__(self, name):
        return Cmd(name)

sh = SH()



if __name__ == '__main__':
    Cmd(*sys.argv[1:]).run()
