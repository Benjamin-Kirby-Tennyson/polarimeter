"""Raw-sample Python/C++ controller oracle traces; no external derived state.

Both sides bootstrap by HELLO, coherent count samples, CONFIGURE and explicit
simulated reference establishment. No state/gate booleans are injected.
"""
from pathlib import Path
import itertools
import json
import sys
import zlib
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from polarimeter_controller import OfflineController,Timing,Readiness,EncoderSample,SimulatedReference
from polarimeter_protocol import FrameMessage,FrameError,encode_frame

NAMES=('retry-stop','stop-arm','arm-stop','multi-change','heartbeat-scope','protocol-recovery',
 'id-conflict','lease-boundary','duration-boundary','stale-session','completion-recovery','backward-clock',
 'rx-overflow','tx-overflow','time-overflow','whitespace-conflict','stale-current-hb','current-stale-hb',
 'stop-same-id','same-id-stop','duplicate-disarm','multiple-hello','identity-exhaustion',
 'feedback-recovery','stale-reference','continuity-reset','count-rate','signed-delta-overflow',
 'negative-direction','velocity-window','duplicate-sample','calibration-change','external-release',
 'travel-recovery','overflow-recovery','fault-terminal-precedence','request-exhaustion','cache-scope')


def traces():
    result=[]
    for name in NAMES:
        policy=dict(lease=200000,request=200000,feedback=50000,cycle=20000,motion=20000000)
        readiness=dict(stationary_band=.1,stationary_dwell=10000,max_count_rate=5e6,velocity_window=20000)
        seed=2**64-1 if name=='identity-exhaustion' else 100
        ids=itertools.count(seed)
        def identity_factory():
            n=next(ids)
            if n>=2**64-1:raise ValueError('Explicit mock identity source exhausted')
            return f'{n:032x}'
        c=OfflineController(timing=Timing(*policy.values()),readiness=Readiness(.1,10000,5e6,20000),boot_id='1'*32,id_factory=identity_factory)
        config=dict(config_id='2'*32,calibration_id='3'*32,counts_per_qwp_rev=54000,max_speed_deg_s=60,
                    max_acceleration_deg_s2=100,min_angle_deg=-360,max_angle_deg=720)
        base_count=-(2**63) if name=='signed-delta-overflow' else 0
        direction=-1 if name=='negative-direction' else 1
        now=-5000;rid=0;raw_count=base_count;continuity='a'*32
        steps=[];retained=None;active_motion=None
        initial=dict(boot=c.boot_id,seed=seed,policy=policy,readiness=readiness)
        def message(kind,**changes):
            nonlocal rid
            rid+=1
            m=dict(v=1,mode='SIMULATED',type=kind,request_id=str(rid),boot_id=c.boot_id,session_id=c.session_id)
            if kind=='HELLO':m.update(boot_id=None,session_id=None,host_nonce='4'*32)
            elif kind not in ('STOP','GET_STATUS'):m['not_after_us']=str(max(0,now)+150000)
            m.update(changes)
            return FrameMessage(encode_frame(m))
        def snapshot():
            f=c.feedback
            return dict(state=c.state,generation=c.arm_generation,config_generation=c.config_generation,session=c.session_id or '',
                challenge='' if c.challenge is None else c.challenge[0],lease=c.lease_end or 0,end=c.motion_end or 0,
                active=c.output.active,faults=sorted(c.faults),reference='' if c._reference is None else c._reference.epoch,
                fresh=c._fresh() if c._now is not None else False,inside=c._inside() if c._now is not None else False,
                stationary=c._stationary() if c._now is not None else False,
                angle=None if f is None else f.angle_deg,velocity=None if f is None else f.velocity_deg_s,terminal=retained)
        def tick(events=(),*,delta=5000,external=False,complete=False,capacity=8,count=None,sample=True,valid=True,
                 continuity_change=None,sample_time=None,has_count=True):
            nonlocal now,retained,active_motion,raw_count,continuity
            now+=delta
            if count is not None:raw_count=count
            if continuity_change:continuity=continuity_change
            raw=None if not sample else dict(time=now if sample_time is None else sample_time,count=raw_count if has_count else None,
                                             continuity=continuity,valid=valid)
            frames=[];parsed=[]
            for e in events:
                if isinstance(e,FrameError):frames.append('BAD');parsed.append(e)
                else:frames.append(e.frame.hex());parsed.append(FrameMessage(e.frame))
            feedback=None if raw is None else EncoderSample(raw['time'],raw['count'],raw['continuity'],raw['valid'])
            replies=c.cycle(parsed,now_us=now,feedback=feedback,external_inhibit=external,tx_capacity=capacity)
            if c.state=='RUNNING' and active_motion is None:
                active_motion=dict(boot=c.boot_id,session=c.session_id,arm=c.arm_generation,motion=int(c.motion_request_id))
            did_complete=False
            if complete:did_complete=c.consider_simulated_completion(position_band_deg=.1,velocity_band_deg_s=.2,dwell_us=0)
            if active_motion and c.state!='RUNNING':
                retained=dict(active_motion,time=now,outcome='FAULT' if c.state=='FAULT' else 'COMPLETED' if did_complete else 'CANCELLED');active_motion=None
            if retained and retained['session']!=c.session_id:retained=None
            expected=snapshot();expected['outcomes']=[r['outcome'] for r in replies]
            steps.append(dict(now=now,frames=frames,sample=raw,external=external,complete_fixture=complete,capacity=capacity,expected=expected))
        def reference(epoch='6'*32,calibration='3'*32,count_zero=None,angle_zero=0):
            nonlocal raw_count
            args=dict(epoch=epoch,calibration=calibration,count_zero=base_count if count_zero is None else count_zero,angle_zero=angle_zero,direction=direction)
            try:c.establish_reference(SimulatedReference(epoch,calibration,args['count_zero'],angle_zero,direction));accepted=True
            except ValueError:accepted=False
            steps.append(dict(reference=args,accepted=accepted,expected=snapshot()))
        def arm():tick([message('ARM',config_id=c.config['config_id'],challenge=c.challenge[0])])
        def move(target=10,duration='1000000'):
            e=message('MOVE_TO',arm_generation=str(c.arm_generation),target_deg=target,speed_deg_s=30,acceleration_deg_s2=60,duration_us=duration);tick([e]);return e
        def rest(n=8):
            for _ in range(n):tick()
        tick([message('HELLO')])
        if name=='identity-exhaustion':
            result.append(dict(name=name,initial=initial,steps=steps));continue
        rest(4)
        tick([message('CONFIGURE',config_generation='0',config=config)])
        reference();tick([message('GET_STATUS')])
        if name=='retry-stop':
            arm();e=move();tick([e]);tick([message('GET_STATUS')]);tick([message('STOP')]);tick([message('GET_STATUS')])
        elif name in ('stop-arm','arm-stop','stop-same-id','same-id-stop'):
            e=message('ARM',config_id=config['config_id'],challenge=c.challenge[0]);stop=message('STOP',**({'request_id':e.message['request_id']} if 'same' in name else {}))
            tick([stop,e] if name in ('stop-arm','stop-same-id') else [e,stop])
        elif name=='multi-change':tick([message('ARM',config_id=config['config_id'],challenge=c.challenge[0]),message('MOVE_TO',arm_generation=str(c.arm_generation+1),target_deg=10,speed_deg_s=30,acceleration_deg_s2=60,duration_us='1000000')])
        elif name in ('heartbeat-scope','stale-current-hb','current-stale-hb'):
            arm();move()
            stale=message('HEARTBEAT',request_id='1',heartbeat_seq='1',arm_generation='0')
            current=message('HEARTBEAT',request_id='1',heartbeat_seq='1',arm_generation=str(c.arm_generation))
            if name=='heartbeat-scope':tick([stale]);tick([current]);tick([current]);tick([message('STOP')])
            else:tick([stale,current] if name=='stale-current-hb' else [current,stale])
        elif name=='protocol-recovery':
            arm();move();tick([FrameError('fixture'),message('STOP')]);tick([message('GET_STATUS')]);tick([message('CLEAR_FAULT')])
        elif name in ('id-conflict','whitespace-conflict'):
            arm();e=move()
            if name=='id-conflict':m=e.message;m['target_deg']=11;bad=FrameMessage(encode_frame(m))
            else:
                payload=json.dumps(e.message).encode();covered=b'QWP1 '+str(len(payload)).encode()+b' '+payload
                bad=FrameMessage(covered+f' {zlib.crc32(covered):08X}\n'.encode())
            tick([bad])
        elif name=='lease-boundary':arm();move();tick(delta=c.lease_end-now)
        elif name=='duration-boundary':arm();move(duration='10000');tick(delta=10000)
        elif name=='stale-session':old=message('GET_STATUS');tick([message('HELLO')]);tick([old])
        elif name=='completion-recovery':arm();move(target=0);tick(complete=True);tick([message('GET_STATUS')]);tick([message('STOP')]);tick([message('GET_STATUS')])
        elif name=='backward-clock':tick(delta=-1)
        elif name in ('rx-overflow','overflow-recovery'):
            arm();move();tick([message('GET_STATUS') for _ in range(9)])
            if name=='overflow-recovery':tick([message('CLEAR_FAULT')]);tick([message('GET_STATUS')]);tick([message('CLEAR_FAULT')])
        elif name=='tx-overflow':arm();move();tick([message('GET_STATUS'),message('STOP')],capacity=1)
        elif name=='time-overflow':tick(delta=2**64-1-now-10)
        elif name=='duplicate-disarm':arm();e=message('DISARM');tick([e,e])
        elif name=='multiple-hello':tick([message('HELLO'),message('HELLO')])
        elif name=='feedback-recovery':
            arm();move();tick(valid=False,has_count=False);rest();tick([message('CLEAR_FAULT')]);reference();reference('7'*32);tick([message('CLEAR_FAULT')])
        elif name=='stale-reference':
            e=message('ARM',config_id=config['config_id'],challenge=c.challenge[0])
            for _ in range(10):tick(sample=False)
            tick([e],sample=False)
        elif name=='continuity-reset':tick(continuity_change='b'*32);rest();reference('7'*32);tick([message('CLEAR_FAULT')])
        elif name=='count-rate':tick(count=1000000)
        elif name=='signed-delta-overflow':tick(count=2**63-1)
        elif name=='negative-direction':tick(count=150);tick(count=300)
        elif name=='velocity-window':
            for n in range(1,9):tick(count=150*n)
        elif name=='duplicate-sample':old=now;tick(sample_time=old);tick(count=1,sample_time=old)
        elif name=='calibration-change':
            old_challenge=c.challenge[0]
            tick([message('CONFIGURE',config_generation='1',config=dict(config,counts_per_qwp_rev=54001))])
            tick([message('CONFIGURE',config_generation='1',config=dict(config,counts_per_qwp_rev=54001,calibration_id='9'*32))])
            tick([message('ARM',config_id=config['config_id'],challenge=old_challenge)]);reference('7'*32,calibration='9'*32)
        elif name=='external-release':
            arm();move();tick(external=True);tick([message('CLEAR_FAULT')],external=True);tick([message('CLEAR_FAULT')])
        elif name=='travel-recovery':
            for n in range(1,9):tick(count=14000*n)
            reference('7'*32,count_zero=raw_count)
            tick([message('CLEAR_FAULT')]);tick(count=98000);rest();tick([message('CLEAR_FAULT')])
        elif name=='fault-terminal-precedence':arm();move(target=0);tick(external=True,complete=True)
        elif name=='request-exhaustion':
            tick([message('GET_STATUS',request_id=str(2**64-1))]);tick([message('HELLO')]);rest();reference('7'*32);tick([message('GET_STATUS')]);tick([message('CLEAR_FAULT')])
        elif name=='cache-scope':
            old=message('GET_STATUS');tick([old])
            for _ in range(10):tick([message('GET_STATUS')])
            tick([old]);tick([message('HELLO')]);tick([old])
        result.append(dict(name=name,initial=initial,steps=steps))
    return result


def cpp(data):
    q=lambda x:json.dumps(x)
    u=lambda x:str(x)+'ULL'
    def i64(n):return '(-9223372036854775807LL-1)' if n==-(2**63) else str(n)+'LL'
    number=lambda x:'std::nullopt' if x is None else f'std::optional<double>{{{x!r}}}'
    terminal=lambda t:'Terminal{}' if not t else 'Terminal{true,'+','.join([q(t['boot']),q(t['session']),q(t['outcome']),u(t['arm']),u(t['motion']),u(t['time'])])+'}'
    frame_map={}
    for trace in data:
        for step in trace['steps']:
            for frame in step.get('frames',[]):
                if frame!='BAD' and frame not in frame_map:frame_map[frame]=len(frame_map)
    lines=['#pragma once','#include "../main/controller.hpp"','namespace qwp {',
        'struct TraceResult {unsigned passed=0,failed=0;const char* first_failure=nullptr;};',
        'struct Expected {std::string state;std::uint64_t generation,config_generation;std::string session,challenge;std::uint64_t lease,end;bool active;std::set<std::string> faults;std::vector<std::string> outcomes;std::string reference;bool fresh,inside,stationary;std::optional<double> angle,velocity;Terminal terminal;};',
        'inline bool close_value(std::optional<double> a,std::optional<double> b){return a.has_value()==b.has_value()&&(!a||(std::isfinite(*a)&&std::abs(*a-*b)<=1e-7*std::max(1.0,std::abs(*b))));}',
        'inline bool matches(const Controller& c,const Expected& e){const auto& t=c.terminal;const auto& r=e.terminal;return c.state==e.state&&c.generation==e.generation&&c.config_generation==e.config_generation&&c.session==e.session&&c.challenge==e.challenge&&c.lease==e.lease&&c.end==e.end&&c.logical_active==e.active&&!c.output.active&&c.faults==e.faults&&c.outcomes==e.outcomes&&(c.encoder.reference?c.encoder.reference->epoch:"")==e.reference&&c.fresh()==e.fresh&&c.inside()==e.inside&&c.stationary()==e.stationary&&close_value(c.encoder.angle,e.angle)&&close_value(c.encoder.velocity,e.velocity)&&t.present==r.present&&(!r.present||(t.boot==r.boot&&t.session==r.session&&t.arm==r.arm&&t.motion==r.motion&&t.time==r.time&&t.outcome==r.outcome));}']
    for frame,index in frame_map.items():
        lines.append(f'inline constexpr std::uint8_t trace_frame_{index}[]={{'+','.join(str(x) for x in bytes.fromhex(frame))+'};')
    lines+=['inline TraceResult controller_traces(){TraceResult summary;']
    for trace in data:
        initial=trace['initial'];p=initial['policy'];r=initial['readiness']
        lines+=['{Controller c(Policy{'+','.join(u(x) for x in p.values())+'},EncoderPolicy{'+','.join([str(r['stationary_band']),str(r['max_count_rate']),u(r['stationary_dwell']),u(r['velocity_window'])])+'},'+q(initial['boot'])+','+u(initial['seed'])+');']
        for index,step in enumerate(trace['steps']):
            lines+=['{']
            reference_accepted=''
            if 'reference' in step:
                r=step['reference']
                lines+=['const bool accepted=c.establish_reference(Reference{'+','.join([q(r['epoch']),q(r['calibration']),i64(r['count_zero']),str(r['angle_zero']),str(r['direction'])])+'});']
                reference_accepted='accepted=='+str(step['accepted']).lower()+' && '
            else:
                lines+=['std::vector<Command> batch;std::size_t errors=0;']
                for frame in step['frames']:
                    if frame=='BAD':lines+=['++errors;']
                    else:
                        name='trace_frame_'+str(frame_map[frame])
                        lines+=['{Command m;if(!decode('+name+',sizeof('+name+'),m))++errors;else batch.push_back(m);}']
                s=step['sample']
                sample='std::nullopt' if s is None else 'std::optional<RawSample>{RawSample{'+','.join([u(s['time']),i64(s['count'] or 0),q(s['continuity']),str(s['valid']).lower(),str(s['count'] is not None).lower()])+'}}'
                lines+=['c.cycle('+','.join([u(step['now']),'batch',sample,'errors>0',str(step['external']).lower(),str(step['capacity']),'false','errors'])+');']
                if step['complete_fixture']:lines+=['c.complete_fixture_only('+u(step['now'])+');']
            e=step['expected']
            values=[q(e['state']),u(e['generation']),u(e['config_generation']),q(e['session']),q(e['challenge']),u(e['lease']),u(e['end']),str(e['active']).lower(),
                '{'+','.join(q(x) for x in e['faults'])+'}',
                '{'+','.join(q(x) for x in e.get('outcomes',[]))+'}',q(e['reference']),str(e['fresh']).lower(),str(e['inside']).lower(),str(e['stationary']).lower(),number(e['angle']),number(e['velocity']),terminal(e['terminal'])]
            # Reference fixture actions do not consume/reset the previous ACKs.
            if 'reference' in step:values[9]='c.outcomes'
            lines+=['const Expected expected{'+','.join(values)+'};',
                'if('+reference_accepted+'matches(c,expected)){++summary.passed;}else{++summary.failed;if(!summary.first_failure)summary.first_failure='+q(trace['name']+':'+str(index))+';}','}']
        lines+=['}']
    return '\n'.join(lines+['return summary;}','} // namespace qwp',''])


if __name__=='__main__':
    data=traces()
    (ROOT/'firmware/shared/controller-traces.json').write_text(json.dumps(data,indent=2)+'\n')
    (ROOT/'firmware/shared/controller-traces.hpp').write_text(cpp(data))
    print(f'{len(data)} raw-sample/reference traces; {sum(len(t["steps"]) for t in data)} compared actions')
