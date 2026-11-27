"""No physical ports: deterministic byte streams, clocks and metadata only."""
from dataclasses import FrozenInstanceError
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from polarimeter_serial import (Framing, HARDWARE_LOCKED, HardwareLockedError,
                                MockSerialBackend, SerialConnection, SIMULATED,
                                State, USBDevice, USBSerialBackend,
                                discover_usb_devices)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class DiscoveryAndLockTests(unittest.TestCase):
    def test_import_and_physical_backend_never_import_serial_or_open(self):
        code = ("import sys; from polarimeter_serial import *; "
                "b = USBSerialBackend(USBDevice('/dev/DO_NOT_OPEN', 0x10c4, 0xea60, '0001', '1-1.2')); "
                "c = SerialConnection(b, Framing(b'\\n')); "
                "assert c.connect() is False; assert c.status.state == State.LOCKED; "
                "c.disconnect(); assert 'serial' not in sys.modules")
        result = subprocess.run([sys.executable, "-B", "-c", code],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_usb_backend_remains_locked_on_repeated_calls(self):
        device = USBDevice('/dev/DO_NOT_OPEN', 0x10c4, 0xea60, '0001', '1-1.2')
        backend = USBSerialBackend(device)
        c = SerialConnection(backend, Framing(b'\n'))
        for _ in range(3):
            self.assertFalse(c.connect())
            self.assertEqual(c.status.state, State.LOCKED)
            self.assertEqual(c.status.mode, HARDWARE_LOCKED)
            self.assertIn('reset', c.status.detail)
            self.assertIsNone(c.take_frame())
        with self.assertRaises(HardwareLockedError):
            backend.read(1)
        with self.assertRaises(HardwareLockedError):
            backend.connect()
        # The session layer also blocks any backend declaring locked hardware.
        locked = Mock(mode=HARDWARE_LOCKED)
        c = SerialConnection(locked, Framing(b'\n'))
        self.assertFalse(c.connect())
        locked.connect.assert_not_called()
        locked.read.assert_not_called()
        for method in ('write', 'move_to', 'stop'):
            self.assertFalse(hasattr(backend, method))

    def test_enumeration_matches_links_without_constructing_serial(self):
        # Temporary regular files stand in for device nodes, never real ports.
        import serial
        from serial.tools import list_ports
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            device = root / 'ttyUSB0'
            device.touch()
            for name in ('by-id', 'by-path'):
                (root / name).mkdir()
                (root / name / 'stable').symlink_to(device)
                (root / name / 'other').symlink_to(root / 'ttyUSB1')
            entry = SimpleNamespace(device=str(device), vid=0x10c4, pid=0xea60,
                                    serial_number='0001', location='1-1.2')
            non_usb = SimpleNamespace(device='/dev/ttyS0', vid=None, pid=None)
            def path_factory(value):
                if str(value).startswith('/dev/serial/'):
                    return root / Path(value).name
                return Path(value)
            with patch.object(list_ports, 'comports', return_value=[non_usb, entry]), \
                 patch.object(serial, 'Serial', side_effect=AssertionError('must not open')), \
                 patch('polarimeter_serial.Path', side_effect=path_factory):
                found = discover_usb_devices()
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].serial_number, '0001')
            self.assertEqual(found[0].by_id, (str(root / 'by-id/stable'),))
            self.assertEqual(found[0].by_path, (str(root / 'by-path/stable'),))
            self.assertEqual(device.stat().st_size, 0)

    def test_discovery_empty_and_permission_error_are_not_mock_fallback(self):
        from serial.tools import list_ports
        with patch.object(list_ports, 'comports', return_value=[]):
            self.assertEqual(discover_usb_devices(), ())
        with patch.object(list_ports, 'comports', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                discover_usb_devices()

    def test_legacy_hardware_arguments_cannot_bypass_lock_or_create_csv(self):
        import polarimeter
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'never.csv'
            args = ['polarimeter.py', '--port', '/dev/DO_NOT_OPEN', '--baud', '12345',
                    '--format', 'float', '--output', str(output), '--seconds', '1', '--no-plot']
            with patch.object(sys, 'argv', args), patch('sys.stderr', new_callable=io.StringIO) as err, \
                 patch.dict(sys.modules, {'serial': None}):
                with self.assertRaises(SystemExit) as raised:
                    polarimeter.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn('Physical serial connection disabled', err.getvalue())
            self.assertFalse(output.exists())


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.backend = MockSerialBackend()
        self.connection = SerialConnection(self.backend, Framing(b'\n'), clock=self.clock)
        self.addCleanup(self.connection.disconnect)

    def connect(self):
        self.assertTrue(self.connection.connect())
        self.assertEqual(self.connection.status.state, State.CONNECTED)

    def test_explicit_connection_and_no_reads_before_connect_or_after_close(self):
        c = self.connection
        with patch.object(self.backend, 'read', wraps=self.backend.read) as read:
            c.poll()
            read.assert_not_called()
            self.connect()
            c.poll()
            read.assert_called_once_with(c.read_size)
            c.disconnect()
            c.poll()
            self.assertEqual(read.call_count, 1)
        self.assertEqual(c.status.state, State.DISCONNECTED)
        self.assertIn('no motor stop', c.status.detail)

    def test_fragmented_and_coalesced_frames_preserve_bytes_and_provenance(self):
        c = self.connection
        self.connect()
        self.backend.feed(b'12')
        c.poll()
        self.assertIsNone(c.take_frame())
        self.clock.advance(.25)
        self.backend.feed(b'3\n\xff\x00\n\npar')
        c.poll()
        frames = [c.take_frame() for _ in range(3)]
        self.assertEqual([f.payload for f in frames], [b'123', b'\xff\x00', b''])
        self.assertEqual([f.sequence for f in frames], [1, 2, 3])
        self.assertEqual({f.mode for f in frames}, {SIMULATED})
        self.assertEqual({f.session for f in frames}, {1})
        self.assertEqual({f.received_at for f in frames}, {100.25})
        with self.assertRaises(FrozenInstanceError):
            frames[0].mode = 'HARDWARE'
        self.assertIsNone(c.take_frame())
        self.backend.feed(b'tial\n')
        c.poll()
        self.assertEqual(c.take_frame().payload, b'partial')

    def test_one_bounded_read_per_poll_and_multibyte_delimiter_at_limit(self):
        backend = MockSerialBackend()
        c = SerialConnection(backend, Framing(b'\r\n', max_payload=3),
                             read_size=2, clock=self.clock)
        self.addCleanup(c.disconnect)
        c.connect()
        backend.feed(b'abc\r\n')
        with patch.object(backend, 'read', wraps=backend.read) as read:
            for _ in range(3):
                c.poll()
            self.assertEqual(read.call_count, 3)
            self.assertEqual(read.call_args.args, (2,))
        self.assertEqual(c.take_frame().payload, b'abc')
        self.assertEqual(c.status.state, State.CONNECTED)

    def test_stale_is_based_on_complete_frames_and_recovers(self):
        c = self.connection
        self.connect()
        self.clock.advance(2)
        self.backend.feed(b'partial')
        self.assertEqual(c.poll().state, State.STALE)
        self.assertEqual(c.last_received_at, 102)
        self.assertIsNone(c.last_frame_at)
        self.backend.feed(b'\n')
        self.assertEqual(c.poll().state, State.CONNECTED)
        self.assertEqual(c.last_frame_at, 102)
        self.clock.advance(2)
        self.assertEqual(c.poll().state, State.STALE)

    def test_timeout_faults_even_when_late_bytes_are_waiting(self):
        c = self.connection
        self.connect()
        self.clock.advance(10)
        self.backend.feed(b'late\n')
        with patch.object(self.backend, 'read', wraps=self.backend.read) as read:
            self.assertEqual(c.poll().state, State.FAULT)
            read.assert_not_called()
        self.assertFalse(self.backend.connected)
        self.assertIsNone(c.take_frame())
        self.assertIn('timeout', c.status.detail)

    def test_unplug_fault_and_explicit_reconnect_discard_old_session_bytes(self):
        c = self.connection
        self.connect()
        self.backend.feed(b'old\ntail')
        c.poll()
        self.backend.unplug()
        self.assertEqual(c.poll().state, State.FAULT)
        self.assertIsNone(c.take_frame())
        self.backend.present = True
        with patch.object(self.backend, 'connect', wraps=self.backend.connect) as connect:
            c.poll()
            connect.assert_not_called()
            self.assertTrue(c.connect())
            connect.assert_called_once()
        self.backend.feed(b'new\n')
        c.poll()
        frame = c.take_frame()
        self.assertEqual((frame.payload, frame.session, frame.sequence), (b'new', 2, 1))

    def test_connect_permission_failure_reports_fault_and_closes(self):
        c = self.connection
        with patch.object(self.backend, 'connect', side_effect=PermissionError('denied')), \
             patch.object(self.backend, 'close', wraps=self.backend.close) as close:
            self.assertFalse(c.connect())
            close.assert_called_once()
        self.assertEqual(c.status.state, State.FAULT)
        self.assertIn('PermissionError: denied', c.status.detail)

    def test_read_failure_clears_queued_data_and_partial_frame(self):
        c = self.connection
        self.connect()
        self.backend.feed(b'queued\npartial')
        c.poll()
        with patch.object(self.backend, 'read', side_effect=OSError('injected read failure')):
            self.assertEqual(c.poll().state, State.FAULT)
        self.assertIn('injected read failure', c.status.detail)
        self.assertIsNone(c.take_frame())
        self.assertFalse(self.backend.connected)

    def test_oversized_complete_and_partial_frames_fault_closed(self):
        for raw in (b'12345', b'12345\n', b'ok\n12345\n'):
            with self.subTest(raw=raw):
                backend = MockSerialBackend()
                c = SerialConnection(backend, Framing(b'\n', max_payload=4), clock=self.clock)
                c.connect()
                backend.feed(raw)
                self.assertEqual(c.poll().state, State.FAULT)
                self.assertIn('Oversized', c.status.detail)
                self.assertIsNone(c.take_frame())
                self.assertFalse(backend.connected)

    def test_queue_overflow_is_explicit_fault_without_silent_sample_loss(self):
        c = SerialConnection(self.backend, Framing(b'\n'), queue_size=1, clock=self.clock)
        self.addCleanup(c.disconnect)
        c.connect()
        self.backend.feed(b'one\ntwo\n')
        self.assertEqual(c.poll().state, State.FAULT)
        self.assertIn('Receive queue full', c.status.detail)
        self.assertIsNone(c.take_frame())

    def test_bad_backend_return_values_fault(self):
        for value in (None, 'text', b'x' * (self.connection.read_size + 1)):
            with self.subTest(value_type=type(value)):
                self.connection.connect()
                with patch.object(self.backend, 'read', return_value=value):
                    self.assertEqual(self.connection.poll().state, State.FAULT)
                self.assertIn('bounded byte-read', self.connection.status.detail)

    def test_close_failure_retains_original_fault_and_blocks_reconnect(self):
        c = self.connection
        self.connect()
        with patch.object(self.backend, 'read', side_effect=OSError('unplugged')), \
             patch.object(self.backend, 'close', side_effect=OSError('close problem')):
            self.assertEqual(c.poll().state, State.FAULT)
            self.assertIn('unplugged', c.status.detail)
            self.assertIn('close problem', c.status.detail)
            with self.assertRaisesRegex(RuntimeError, 'Close failed'):
                c.connect()
            c.disconnect()
            self.assertEqual(c.status.state, State.FAULT)
        c.disconnect()
        self.assertEqual(c.status.state, State.DISCONNECTED)
        self.assertTrue(c.connect())

    def test_double_connect_is_rejected_and_disconnect_is_idempotent(self):
        c = self.connection
        self.connect()
        with self.assertRaises(RuntimeError):
            c.connect()
        c.disconnect()
        c.disconnect()
        self.assertEqual(c.status.state, State.DISCONNECTED)

    def test_event_history_is_bounded_and_idle_polls_do_not_flood_it(self):
        c = SerialConnection(self.backend, Framing(b'\n'), event_size=3, clock=self.clock)
        self.addCleanup(c.disconnect)
        for _ in range(10):
            c.connect()
            c.disconnect()
        self.assertEqual(len(c.events), 3)
        c.connect()
        before = tuple(c.events)
        for _ in range(100):
            c.poll()
        self.assertEqual(tuple(c.events), before)

    def test_invalid_framing_limits_timeouts_and_modes_are_rejected(self):
        for delimiter in (b'', b'x' * 17, '\n', None):
            with self.assertRaises(ValueError):
                Framing(delimiter)
        for value in (0, -1, True, 1.5, 65537):
            with self.assertRaises(ValueError):
                Framing(b'\n', max_payload=value)
        for stale, fault in ((0, 1), (2, 2), (3, 2), (float('nan'), 10), (2, float('inf'))):
            with self.assertRaises(ValueError):
                SerialConnection(self.backend, Framing(b'\n'), stale_after=stale, fault_after=fault)
        for key in ('read_size', 'queue_size', 'event_size'):
            with self.assertRaises(ValueError):
                SerialConnection(self.backend, Framing(b'\n'), **{key: 0})
        with self.assertRaises(ValueError):
            SerialConnection(SimpleNamespace(mode='HARDWARE'), Framing(b'\n'))

    def test_mock_storage_is_bounded_and_no_write_api_exists(self):
        backend = MockSerialBackend(capacity=3)
        backend.feed(b'abc')
        with self.assertRaises(BufferError):
            backend.feed(b'd')
        with self.assertRaises(TypeError):
            backend.feed('abc')
        backend.connect()
        self.assertEqual(backend.read(2), b'ab')
        self.assertEqual(backend.read(2), b'c')
        self.assertEqual(backend.read(2), b'')
        self.assertFalse(hasattr(backend, 'write'))
        backend.close()


if __name__ == '__main__':
    unittest.main()
