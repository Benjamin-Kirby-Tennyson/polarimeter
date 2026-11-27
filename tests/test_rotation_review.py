"""Regressions from the independent Stage 4 source review; no hardware I/O."""
import json
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from polarimeter_optical import OpticalClock, RotationLog
from polarimeter_protocol import FrameMessage, encode_frame
from polarimeter_rotation_session import RotationSession, RotationWorker
from tests.test_rotation import linked, running
from tests import test_rotation as rotation_fixtures


class TerminalTests(unittest.TestCase):
    def test_drop_exact_completion_recovers_retained_identity_and_stop_does_not_recomplete(self):
        link, host = linked()
        host.plan.update(end_deg=10,speed_deg_s=10,acceleration_deg_s2=30,plateau_start_deg=3,plateau_end_deg=7,duration_us='10000000')
        original = link.poll
        lost = []
        def poll(now):
            frame = original(now)
            if frame is not None and FrameMessage(frame).message['terminal'] and not lost:
                lost.append(FrameMessage(frame).message['terminal'])
                return None
            return frame
        link.poll = poll
        for now in range(0, 50000000, 5000):
            host.tick(now)
            if host.phase in ('COMPLETED', 'UNKNOWN', 'FAULT'):
                break
        self.assertEqual(host.phase, 'COMPLETED', host.detail)
        self.assertEqual(tuple(lost[0]), host.terminal)
        self.assertEqual(host.terminal[2:5], (host.session_id,host.boot_id,host.arm_generation))
        terminal = host.terminal
        host.tick(now+5000)  # retained duplicate with a newer status sequence
        self.assertEqual(host.terminal, terminal)
        host.cancel(now+5000)
        host.tick(now+10000)
        self.assertEqual(host.phase, 'CANCELLED')
        self.assertIsNone(host.terminal)
        self.assertEqual(tuple(link.retained_terminal[k] for k in
            ('request_id','outcome','session_id','boot_id','arm_generation','time_us')), terminal)

    def test_delay_within_status_budget_and_unrecoverable_inactive(self):
        link, host, now = running()
        link.delay_us = 10000
        for t in range(now+5000,now+80000,5000):
            host.tick(t)
        self.assertEqual(host.phase, 'SCANNING')
        p = dict(host.latest, seq=str(host._sequence+1), active=False,duty=0,state='STOPPED',terminal=None)
        with self.assertRaisesRegex(ValueError, 'inactive'):
            host._receive(encode_frame(p),t)

    def test_delayed_terminal_then_conflicting_duplicate_cannot_override(self):
        link,host=linked()
        host.plan.update(end_deg=10,speed_deg_s=10,acceleration_deg_s2=30,plateau_start_deg=3,plateau_end_deg=7,duration_us='10000000')
        original=link.poll
        held=[]
        def poll(now):
            frame=original(now)
            if held and now>=held[0][0]:return held[0][1]
            if frame is not None and FrameMessage(frame).message['terminal']:
                if not held:held.append((now+20000,frame))
                return None
            return frame
        link.poll=poll
        for now in range(0,12000000,5000):
            host.tick(now)
            if host.phase in ('COMPLETED','UNKNOWN','FAULT'):break
        self.assertEqual(host.phase,'COMPLETED',host.detail)
        p=dict(host.latest,seq=str(host._sequence+1))
        p['terminal']=list(p['terminal']);p['terminal'][5]=str(int(p['terminal'][5])-1)
        with self.assertRaisesRegex(ValueError,'Conflicting terminal'):host._receive(encode_frame(p),now)
        # A new fault must never preserve a COMPLETED presentation.
        p=dict(host.latest,seq=str(host._sequence+1),state='FAULT',faults=['STALL'])
        with self.assertRaises(ValueError):host._receive(encode_frame(p),now)

    def test_phase_deadlines_cannot_be_extended_by_live_status(self):
        for phase in ('WAIT_READY','WAIT_REFERENCE','WAIT_CHALLENGE'):
            link,host=linked()
            original=link.poll
            def poll(now):
                frame=original(now)
                if frame is None: return None
                p=FrameMessage(frame).message
                if phase=='WAIT_READY': p['ready']=False
                if phase=='WAIT_REFERENCE': p['ref']=None
                if phase=='WAIT_CHALLENGE': p['challenge']=None
                return encode_frame(p)
            link.poll=poll
            for now in range(0,800000,5000): host.tick(now)
            self.assertEqual(host.phase,'UNKNOWN', phase)
            self.assertIn('phase deadline',host.detail)
        link,host,now=running()
        host._scan_deadline=now+20000
        for t in range(now+5000,now+30000,5000):host.tick(t)
        self.assertEqual(host.phase,'UNKNOWN')

    def test_heartbeat_rejected_or_missing_ack_with_live_status(self):
        for rejected in (True,False):
            link,host,now=running()
            original=link.poll
            def poll(t):
                frame=original(t)
                if frame is None:return None
                p=FrameMessage(frame).message
                p['ack']=[a if a[0]!='HEARTBEAT' else [a[0],a[1],'WRONG_ARM_OR_SESSION'] for a in p['ack']] if rejected else [a for a in p['ack'] if a[0]!='HEARTBEAT']
                return encode_frame(p)
            link.poll=poll
            for t in range(now+5000,now+300000,5000):host.tick(t)
            self.assertEqual(host.phase,'UNKNOWN',host.detail)
            self.assertFalse(link.harness.controller.output.active)

    def test_identity_generation_terminal_and_fault_contradictions(self):
        for change in ({'generation':'999'}, {'motion':'999'}, {'state':'DISARMED','active':False,'duty':0,'motion':None},
                       {'faults':['STALL']}, {'terminal':['5','CANCELLED','2'*32,'1'*32,'1','1']}):
            link,host,now=running()
            packet=dict(host.latest,seq=str(host._sequence+1),**change)
            with self.assertRaises(ValueError):host._receive(encode_frame(packet),now)
        a,b=RotationSession(simulate=True),RotationSession(simulate=True)
        try:
            self.assertNotEqual(a.experiment_id,b.experiment_id)
            self.assertNotEqual(a.host.nonce,b.host.nonce)
            self.assertNotEqual(a.link.harness.controller.boot_id,b.link.harness.controller.boot_id)
            a.step();b.step()
            self.assertNotEqual(a.host.session_id,b.host.session_id)
        finally:a.finish();b.finish()

    def test_stop_at_full_capacity_inhibits_even_without_ack(self):
        link,host,now=running()
        frame=encode_frame(dict(v=1,mode='SIMULATED',type='GET_STATUS',request_id='100',boot_id=host.boot_id,session_id=host.session_id))
        for _ in range(8):link.send(frame)
        link.suppress_responses=True
        host.cancel(now)
        for t in range(now+5000,now+300000,5000):host.tick(t)
        self.assertFalse(link.harness.controller.output.active)
        self.assertEqual(host.phase,'UNKNOWN')


class OpticalReviewTests(unittest.TestCase):
    def setUp(self): self.fixture=rotation_fixtures.OpticalTests()

    def test_in_bound_mapping_error_changes_association_not_detector_measurement(self):
        a,b=self.fixture.acquisition(),self.fixture.acquisition()
        b.mapping=OpticalClock(10050,50,100)
        aa=[];bb=[]
        for i in range(40):
            x,y=a.record(self.fixture.packet(i)),b.record(self.fixture.packet(i))
            if x is not None and y is not None and x['quality']==y['quality']=='VALID':aa.append(x);bb.append(y)
        self.assertGreater(len(aa),3)
        self.assertEqual([x['intensity_au'] for x in aa],[x['intensity_au'] for x in bb])
        self.assertNotEqual(aa[-1]['estimated_angle_deg'],bb[-1]['estimated_angle_deg'])
        self.assertEqual(aa[-1]['optical_truth_velocity_deg_s'],bb[-1]['optical_truth_velocity_deg_s'])

    def test_delayed_status_cannot_rewind_independent_physics_timeline(self):
        a=self.fixture.acquisition()
        for i in range(40):a.record_truth(i*5000,180+30*i*.005+5)
        last=a.truth_history[-1]['time']
        for i in range(30):a.record(self.fixture.packet(i))
        self.assertEqual(a.truth_history[-1]['time'],last)
        self.assertEqual(len(a.truth_history),40)
        self.assertGreater(a.valid,0)

    def test_flagged_measurement_kept_and_zero_span_not_divided(self):
        for zero in (False,True):
            a=self.fixture.acquisition(mapping=zero)
            with patch.object(OpticalClock,'to_host',return_value=1) if zero else patch('builtins.id',wraps=id):
                samples=[x for i in range(30) if (x:=a.record(self.fixture.packet(i))) is not None]
            last=samples[-1]
            self.assertEqual(last['quality'],'INVALID_MAPPED_SPAN' if zero else 'CLOCK_UNMAPPED')
            self.assertIsNotNone(last['intensity_au'])
            self.assertIsNone(last['estimated_angle_deg'])

    def test_full_identity_and_gap_coverage_not_only_span(self):
        a=self.fixture.acquisition()
        samples=[]
        for i in range(80):
            if 30<=i<40:continue
            p=self.fixture.packet(i)
            if i>=60:p['session_id']='8'*32  # same motion ID, distinct session
            x=a.record(p)
            if x is not None:samples.append(x)
        self.assertTrue(any(x['quality']=='REFERENCE_OR_MOTION_CHANGED' for x in samples))
        last=samples[-1]
        self.assertEqual(last['session_id'],'8'*32)
        self.assertEqual(last['reference_epoch'],'6'*32)
        self.assertIsNotNone(last['bracket_sequences'])
        self.assertGreater(a.span_deg,a.contiguous_deg)
        self.assertGreater(a.missing,0)
        self.assertEqual(a.expected,a.valid+a.rejected+a.missing)

    def test_backwards_time_and_uncertainty_coverage(self):
        a=self.fixture.acquisition()
        for i in range(30):a.record(self.fixture.packet(i))
        a.record(self.fixture.packet(5,seq='100'))
        self.assertEqual(len(a.history),1)
        a=self.fixture.acquisition()
        a.mapping=OpticalClock(10000,50,1000000)
        samples=[x for i in range(30) if (x:=a.record(self.fixture.packet(i))) is not None]
        self.assertEqual(a.valid,0)
        self.assertTrue(all(x['estimated_angle_deg'] is None for x in samples))


class LifecycleTests(unittest.TestCase):
    def test_final_flush_failure_publishes_unknown_only(self):
        with tempfile.TemporaryDirectory() as folder:
            s=RotationSession(simulate=True,directory=folder)
            s.host.phase='COMPLETED'
            original=s.logger.raw_file
            s.logger.raw_file=Mock(wraps=original)
            s.logger.raw_file.flush.side_effect=OSError('final flush full')
            with self.assertRaises(OSError):s.finish()
            m=json.loads(s.logger.manifest_path.read_text())
            self.assertEqual(m['phase'],'UNKNOWN')
            self.assertFalse(m['data_complete'])
            self.assertIn('final flush full',m['detail'])
            self.assertEqual(s.host.phase,'UNKNOWN')

    def test_manifest_failure_leaves_no_completed_record_and_closes_data(self):
        with tempfile.TemporaryDirectory() as folder:
            s=RotationSession(simulate=True,directory=folder)
            s.host.phase='COMPLETED'
            with patch('polarimeter_optical.os.replace',side_effect=OSError('manifest full')):
                with self.assertRaises(OSError):s.finish()
            self.assertFalse(s.logger.manifest_path.exists())
            self.assertTrue(s.logger.raw_file.closed)
            self.assertTrue(s.logger.sample_file.closed)
            self.assertEqual(s.host.phase,'UNKNOWN')

    def test_initial_header_failure_closes_files_and_reports_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            w=RotationWorker(folder)
            with patch('polarimeter_optical.csv.DictWriter.writeheader',side_effect=OSError('header full')):
                w.start_scan();w.start();w.join(timeout=5)
            self.assertFalse(w.is_alive())
            self.assertEqual(w.snapshots.get_nowait()['phase'],'UNKNOWN')
            self.assertEqual(list(Path(folder).rglob('*-session.json')),[])

    def test_two_starts_reset_clock_publish_identity_and_cancel_races(self):
        with tempfile.TemporaryDirectory() as folder:
            w=RotationWorker(folder);w.start()
            try:
                first=w.start_scan()
                time.sleep(.25)  # first virtual clock > 100ms, no Tk polling
                w.stop_requested.set()
                deadline=time.monotonic()+5
                while w.busy.is_set() and time.monotonic()<deadline:time.sleep(.01)
                self.assertFalse(w.busy.is_set())
                second=w.start_scan()
                self.assertNotEqual(first,second)
                v=w.snapshots.get(timeout=1)
                self.assertEqual(v['experiment_id'],second)
                self.assertNotEqual(v['phase'],'CANCELLED')
                w.stop_requested.set()
                deadline=time.monotonic()+5
                while w.busy.is_set() and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(w.snapshots.get(timeout=1)['experiment_id'],second)
            finally:
                w.shutdown_requested.set();w.join(timeout=5)
                self.assertFalse(w.is_alive())
