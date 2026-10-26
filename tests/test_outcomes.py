"""Offline regression coverage: serial and every ADB boundary are inert."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class SerialError(Exception):
    pass


# Never import pyserial or open hardware, even if it is installed locally.
sys.modules['serial'] = types.SimpleNamespace(SerialException=SerialError)
spec = importlib.util.spec_from_file_location('csctool', Path(__file__).parents[1] / 'csctool.py')
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)


class FakeSerial:
    def __init__(self, replies, failure=None):
        self.replies = iter(replies)
        self.failure = failure
        self.commands = []
        self.in_waiting = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.commands.append(data.decode().strip())
        if self.failure == 'write' and self.commands[-1] == tool.REBOOT_COMMAND:
            raise SerialError('disconnected')
        return len(data)

    def flush(self):
        if self.failure == 'flush' and self.commands[-1] == tool.REBOOT_COMMAND:
            raise SerialError('disconnected')

    def read(self, count):
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply.encode()


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(tool, 'adb', side_effect=AssertionError('ADB forbidden')))
        self.stack.enter_context(patch.object(tool.subprocess, 'run', side_effect=AssertionError('subprocess forbidden')))
        self.stack.enter_context(patch.object(tool, 'check_port_access'))
        self.stack.enter_context(patch.object(tool.time, 'sleep'))

    def run_sequence(self, final, failure=None, replies=None):
        fake = FakeSerial(replies or ['CHANGE TO DDEXE', '', 'OK', '+PRECONFG:2,OK', final], failure)
        with patch.object(tool.serial, 'Serial', return_value=fake, create=True):
            results = tool.send_at_sequence('inert', tool.build_sequence('ABC'))
        return results, fake

    def report(self, results):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            tool.report_change_results('ABC', results)
        return output.getvalue()

    def test_na_ok_transcript_does_not_claim_reboot_or_failed_csc(self):
        results, fake = self.run_sequence('+CFUN=1,1:NA\r\nOK')
        self.assertEqual(results[-1].outcome, 'na')
        output = self.report(results)
        self.assertIn('configuration request for ABC: acknowledged', output)
        self.assertIn('modem returned NA', output)
        self.assertIn('may already have applied', output)
        self.assertNotIn('Phone is rebooting', output)
        self.assertEqual(fake.commands, tool.build_sequence('ABC'))

    def test_reboot_outcomes_remain_unverified(self):
        for reply, outcome, message in [
            ('OK', 'acknowledged', 'only an acknowledgement'),
            ('', 'unconfirmed', 'no conclusive reply'),
            ('unexpected', 'unconfirmed', 'no conclusive reply'),
            ('+CME Error: 3\r\nOK', 'error', 'returned an error'),
            (SerialError('gone'), 'disconnected', 'connection lost'),
            (OSError('gone'), 'disconnected', 'connection lost'),
        ]:
            with self.subTest(reply=reply):
                results, _ = self.run_sequence(reply)
                self.assertEqual(results[-1].outcome, outcome)
                output = self.report(results)
                self.assertIn(message, output)
                self.assertIn('Reboot and active CSC have not been verified', output)
                self.assertIn('reboot it manually', output)

    def test_write_and_flush_failures(self):
        for failure, outcome in [('write', 'send_failed'), ('flush', 'disconnected')]:
            with self.subTest(failure=failure):
                results, _ = self.run_sequence('', failure)
                self.assertEqual(results[-1].outcome, outcome)
                self.assertIn('unconfirmed' if failure == 'write' else 'unverified', self.report(results))

    def test_negative_response_stops_before_reboot(self):
        for negative in ['ERROR', '+CME Error: 3 OK', '+PRECONFG:2,NA\r\nOK']:
            with self.subTest(negative=negative):
                fake = FakeSerial(['OK', '', 'OK', negative])
                with patch.object(tool.serial, 'Serial', return_value=fake, create=True):
                    with self.assertRaises(SystemExit):
                        tool.send_at_sequence('inert', tool.build_sequence('ABC'))
                self.assertNotIn(tool.REBOOT_COMMAND, fake.commands)

    def test_generic_ok_does_not_confirm_configuration(self):
        for reply in ['', 'OK', '+PRECONFG:2,NA OK']:
            output = self.report([tool.ATResult('AT+PRECONFG=2,ABC', reply, tool.classify_reply(reply))])
            self.assertIn('configuration request for ABC: unconfirmed', output)

    def test_cli_uses_results_without_extra_device_operations(self):
        mocks = {
            'check_adb': None, 'require_one_device': 'inert', 'require_samsung': None,
            'current_csc': 'XYZ', 'list_supported_csc': ['ABC'],
            'wait_for_port': ['inert'], 'getprop': '',
        }
        with contextlib.ExitStack() as stack:
            for name, value in mocks.items():
                stack.enter_context(patch.object(tool, name, return_value=value))
            send = stack.enter_context(patch.object(tool, 'send_at_sequence', return_value=[]))
            report = stack.enter_context(patch.object(tool, 'report_change_results'))
            tool.cmd_change(types.SimpleNamespace(csc='ABC', yes=True, port=None))
            send.assert_called_once_with('inert', tool.build_sequence('ABC'))
            report.assert_called_once_with('ABC', [])


if __name__ == '__main__':
    unittest.main()
