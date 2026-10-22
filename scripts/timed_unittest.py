"""Standard-library unittest child with bounded, payload-free external telemetry."""
from __future__ import annotations

import argparse
import faulthandler
import functools
import json
from pathlib import Path
import sys
import threading
import time
import unittest


class Telemetry:
    def __init__(self, path, stacks, deadline):
        self.stream = Path(path).open('x', encoding='utf-8')
        self.stacks = Path(stacks).open('x', encoding='utf-8')
        self.lock = threading.RLock()
        self.active = {}
        self.written = 0
        self.timer = threading.Timer(max(.01, deadline - min(2, deadline / 4)), self.snapshot)
        self.timer.daemon = True

    def emit(self, event, **fields):
        with self.lock:
            data = json.dumps(dict(event=event, monotonic=time.monotonic(), **fields), ensure_ascii=True) + '\n'
            if self.written + len(data) > 8 * 1024 * 1024:
                raise RuntimeError('Test telemetry budget exceeded')
            self.stream.write(data)
            self.stream.flush()
            self.written += len(data)

    def snapshot(self):
        with self.lock:
            self.emit('deadline_snapshot', unfinished=list(self.active))
            # Runs INSIDE the actual unittest child, including its test threads.
            # faulthandler omits locals, environment and model/request payloads.
            faulthandler.dump_traceback(file=self.stacks, all_threads=True)
            self.stacks.flush()

    def close(self):
        self.timer.cancel()
        if self.timer.is_alive():
            self.timer.join(1)
        self.emit('unfinished', test_ids=list(self.active))
        self.stream.close()
        self.stacks.close()


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--telemetry', required=True)
    parser.add_argument('--stacks', required=True)
    parser.add_argument('--deadline', required=True, type=float)
    parser.add_argument('unittest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not 0 < args.deadline <= 420:
        parser.error('deadline must be >0 and <=420 seconds')
    tail = args.unittest_args
    if tail[:1] == ['--']:
        tail = tail[1:]
    if tail[:2] != ['-m', 'unittest']:
        parser.error('expected -- -m unittest followed by ordinary unittest arguments')
    telemetry = Telemetry(args.telemetry, args.stacks, args.deadline)
    originals = {}

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            telemetry.active[test.id()] = {'started': time.monotonic(), 'status': 'passed'}
            telemetry.emit('start', test_id=test.id())
            super().startTest(test)

        def stopTest(self, test):
            value = telemetry.active.pop(test.id())
            telemetry.emit('finish', test_id=test.id(), status=value['status'],
                           seconds=time.monotonic() - value['started'])
            super().stopTest(test)

        def mark(self, test, status):
            if test.id() in telemetry.active:
                telemetry.active[test.id()]['status'] = status

        def addFailure(self, test, err):
            self.mark(test, 'failed'); super().addFailure(test, err)

        def addError(self, test, err):
            self.mark(test, 'error'); super().addError(test, err)

        def addSkip(self, test, reason):
            self.mark(test, 'skipped'); super().addSkip(test, reason)

        def addExpectedFailure(self, test, err):
            self.mark(test, 'expected_failure'); super().addExpectedFailure(test, err)

        def addUnexpectedSuccess(self, test):
            self.mark(test, 'unexpected_success'); super().addUnexpectedSuccess(test)

        def addSubTest(self, test, subtest, err):
            if err is not None:
                self.mark(test, 'failed_subtest')
            super().addSubTest(test, subtest, err)

    def phase(name, original):
        @functools.wraps(original)
        def call(case, *values, **kw):
            start = time.monotonic()
            try:
                return original(case, *values, **kw)
            finally:
                telemetry.emit('phase', test_id=case.id(), phase=name,
                               seconds=time.monotonic() - start)
        return call

    try:
        for name in ('_callSetUp', '_callTestMethod', '_callTearDown', '_callCleanup'):
            originals[name] = getattr(unittest.TestCase, name)
            setattr(unittest.TestCase, name, phase(name, originals[name]))
        telemetry.timer.start()
        runner = unittest.TextTestRunner(verbosity=2, resultclass=Result)
        program = unittest.main(module=None, argv=['unittest', *tail[2:]], testRunner=runner, exit=False)
        result = program.result
        telemetry.emit('summary', tests=result.testsRun, skipped=len(result.skipped),
                       failures=len(result.failures), errors=len(result.errors), passed=result.wasSuccessful())
        return 0 if result.wasSuccessful() else 1
    finally:
        for name, original in originals.items():
            setattr(unittest.TestCase, name, original)
        telemetry.close()


if __name__ == '__main__':
    raise SystemExit(run())
