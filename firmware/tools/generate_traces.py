"""Export admission/state projection of Python controller; no compilation or I/O.

Feedback gates are explicit inputs, not encoder conformance. Target compares
state, generations, ordered ACKs, faults and retained motion identity/outcome.
"""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tests.simulation_fixtures import Scenario
from polarimeter_controller import EncoderSample
from polarimeter_protocol import FrameMessage,FrameError,encode_frame


def traces():
    result=[]
    for name in ('retry-stop','stop-arm','arm-stop','multi-change','heartbeat-scope','protocol-recovery',
                 'id-conflict','lease-boundary','duration-boundary','stale-session','completion-recovery','backward-clock','rx-overflow','tx-overflow','time-overflow','whitespace-conflict'):
        s=Scenario();c=s.controller;now=s.now
        initial=dict(state=c.state,boot=c.boot_id,session=c.session_id,challenge=c.challenge[0],
                     challenge_end=c.challenge[1],generation=c.arm_generation,config_generation=c.config_generation,
                     request_id=c._request_id,now=now,next_identity=102,config=s.config,
                     policy=dict(lease=c.timing.lease_us,request=c.timing.request_us,feedback=c.timing.feedback_us,cycle=c.timing.cycle_us,motion=c.timing.motion_us))
        steps=[];retained=None;active_motion=None
        def message(kind,**changes):
            s.now=now
            return s.message(kind,**changes)
        def tick(events=(),*,delta=5000,external=False,complete=False,capacity=8):
            nonlocal now,retained,active_motion
            now+=delta;s.now=now
            raw=[];parsed=[];invalid=False
            for event in events:
                if isinstance(event,FrameError):raw.append('BAD');parsed.append(event);invalid=True
                else:raw.append(event.frame.hex());parsed.append(event)
            gates=dict(fresh=True,stationary=True,inside=True,reference=c._reference is not None,angle=0)
            replies=c.cycle(parsed,now_us=now,feedback=EncoderSample(now,0,'a'*32,True),external_inhibit=external,tx_capacity=capacity)
            if c.state=='RUNNING' and active_motion is None:
                active_motion=dict(boot=c.boot_id,session=c.session_id,arm=c.arm_generation,motion=int(c.motion_request_id))
            did_complete=False
            if complete:did_complete=c.consider_simulated_completion(position_band_deg=.1,velocity_band_deg_s=.2,dwell_us=0)
            if active_motion is not None and c.state!='RUNNING':
                retained=dict(active_motion,time=now,outcome='FAULT' if c.state=='FAULT' else 'COMPLETED' if did_complete else 'CANCELLED');active_motion=None
            if retained and retained['session']!=c.session_id:retained=None
            steps.append(dict(now=now,frames=raw,gates=gates,external=external,complete=did_complete,capacity=capacity,
                expected=dict(state=c.state,generation=c.arm_generation,config_generation=c.config_generation,
                              session=c.session_id,lease=c.lease_end or 0,end=c.motion_end or 0,
                              active=c.output.active,faults=sorted(c.faults),outcomes=[r['outcome'] for r in replies],terminal=retained)))
        def arm():
            tick([message('ARM',config_id=c.config['config_id'],challenge=c.challenge[0])])
        def move(target=10,duration='1000000'):
            e=message('MOVE_TO',arm_generation=str(c.arm_generation),target_deg=target,speed_deg_s=30,acceleration_deg_s2=60,duration_us=duration);tick([e]);return e
        if name=='retry-stop':
            arm();e=move();tick([e]);tick([message('GET_STATUS')]);tick([message('STOP')]);tick([message('GET_STATUS')])
        elif name in ('stop-arm','arm-stop'):
            batch=[message('STOP'),message('ARM',config_id=c.config['config_id'],challenge=c.challenge[0])]
            tick(batch if name=='stop-arm' else batch[::-1])
        elif name=='multi-change':tick([message('ARM',config_id=c.config['config_id'],challenge=c.challenge[0]),message('MOVE_TO',arm_generation=str(c.arm_generation+1),target_deg=10,speed_deg_s=30,acceleration_deg_s2=60,duration_us='1000000')])
        elif name=='heartbeat-scope':
            arm();move()
            tick([message('HEARTBEAT',request_id='1',heartbeat_seq='1',arm_generation='0')])
            hb=message('HEARTBEAT',request_id='1',heartbeat_seq='1',arm_generation=str(c.arm_generation));tick([hb]);tick([hb]);tick([message('STOP')])
        elif name=='protocol-recovery':
            arm();move();tick([FrameError('fixture malformed frame'),message('STOP')]);tick([message('GET_STATUS')]);tick([message('CLEAR_FAULT')])
        elif name=='id-conflict':
            arm();e=move();m=e.message;m['target_deg']=11;tick([FrameMessage(encode_frame(m))])
        elif name=='lease-boundary':
            arm();move();tick(delta=c.lease_end-now)
        elif name=='duration-boundary':
            arm();move(duration='10000');tick(delta=10000)
        elif name=='stale-session':
            old=message('GET_STATUS');tick([message('HELLO')]);tick([old])
        elif name=='completion-recovery':
            arm();move(target=0);tick(complete=True);tick([message('GET_STATUS')]);tick([message('STOP')]);tick([message('GET_STATUS')])
        elif name=='backward-clock':tick(delta=-1)
        elif name=='rx-overflow':
            arm();move();tick([message('GET_STATUS') for _ in range(9)])
        elif name=='time-overflow':tick(delta=2**64-1-now-10)
        elif name=='whitespace-conflict':
            import zlib
            arm();e=move();payload=json.dumps(e.message).encode();covered=b'QWP1 '+str(len(payload)).encode()+b' '+payload
            tick([FrameMessage(covered+f' {zlib.crc32(covered):08X}\n'.encode())])
        elif name=='tx-overflow':
            arm();move();tick([message('GET_STATUS'),message('STOP')],capacity=1)
        result.append(dict(name=name,initial=initial,steps=steps))
    return result


def cpp(data):
    q=lambda s:json.dumps(s)
    lines=['#pragma once','#include "../main/controller.hpp"','namespace qwp {',
           'struct TraceResult {unsigned passed=0,failed=0;const char* first_failure=nullptr;};',
           'inline TraceResult controller_traces(){TraceResult summary;']
    for trace in data:
        i=trace['initial'];p=i['policy'];cfg=i['config']
        lines+=['{',f'Controller c(Policy{{{p["lease"]}ULL,{p["request"]}ULL,{p["feedback"]}ULL,{p["cycle"]}ULL,{p["motion"]}ULL}});',
            'c.state='+q(i['state'])+';c.boot='+q(i['boot'])+';c.session='+q(i['session'])+';c.challenge='+q(i['challenge'])+';',
            f'c.generation={i["generation"]};c.config_generation={i["config_generation"]};c.request_id={i["request_id"]};c.last={i["now"]};c.has_time=true;c.challenge_end={i["challenge_end"]};c.next_identity={i["next_identity"]};c.configured=c.referenced=true;',
            'c.config=Configuration{'+','.join([q(cfg['config_id']),q(cfg['calibration_id']),str(cfg['counts_per_qwp_rev']),str(cfg['max_speed_deg_s']),str(cfg['max_acceleration_deg_s2']),str(cfg['min_angle_deg']),str(cfg['max_angle_deg'])])+'};']
        for step_index,step in enumerate(trace['steps']):
            e=step['expected'];g=step['gates'];lines+=['{ std::vector<Command> batch;bool invalid=false;']
            for frame in step['frames']:
                if frame=='BAD':lines+=['invalid=true;']
                else:
                    raw=bytes.fromhex(frame)
                    lines+=['{ const std::uint8_t bytes[]={'+','.join(str(x) for x in raw)+'};Command m;if(!decode(bytes,sizeof(bytes),m))invalid=true;else batch.push_back(m);}']
            gates=','.join([str(g[x]).lower() for x in ('fresh','stationary','inside','reference')])+','+str(g['angle'])
            lines+=[f'c.cycle({step["now"]}ULL,batch,Gates{{{gates}}},invalid,{str(step["external"]).lower()},{step["capacity"]});']
            if step['complete']:lines+=[f'c.complete({step["now"]}ULL);']
            check=[f'c.state=={q(e["state"])}',f'c.generation=={e["generation"]}ULL',f'c.config_generation=={e["config_generation"]}ULL',f'c.session=={q(e["session"])}',f'c.lease=={e["lease"]}ULL',f'c.end=={e["end"]}ULL',f'c.logical_active=={str(e["active"]).lower()}', '!c.output.active',
                   'c.outcomes==std::vector<std::string>{'+','.join(q(x) for x in e['outcomes'])+'}',
                   'c.faults==std::set<std::string>{'+','.join(q(x) for x in e['faults'])+'}']
            t=e['terminal']
            if t:check += ['c.terminal.present',f'c.terminal.boot=={q(t["boot"])}',f'c.terminal.session=={q(t["session"])}',f'c.terminal.arm=={t["arm"]}ULL',f'c.terminal.motion=={t["motion"]}ULL',f'c.terminal.time=={t["time"]}ULL',f'c.terminal.outcome=={q(t["outcome"])}']
            else:check+=['!c.terminal.present']
            lines+=['if('+ ' && '.join(check)+'){++summary.passed;}else{++summary.failed;}','}']
        lines+=['}']
    return '\n'.join(lines+['return summary;}','} // namespace qwp',''])


if __name__=='__main__':
    data=traces()
    (ROOT/'firmware/shared/controller-traces.json').write_text(json.dumps(data,indent=2)+'\n')
    (ROOT/'firmware/shared/controller-traces.hpp').write_text(cpp(data))
    print(f'{len(data)} reference traces; {sum(len(t["steps"]) for t in data)} compared cycles')
