"""Generate shared envelope fixtures; offline, dependency-free, no target build."""
import json
from pathlib import Path
import sys
import zlib

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from polarimeter_protocol import encode_frame


def fixtures():
    hello=encode_frame(dict(v=1,mode='SIMULATED',type='HELLO',request_id='1',boot_id=None,
                            session_id=None,host_nonce='1'*32))
    stop=encode_frame(dict(v=1,mode='SIMULATED',type='STOP',request_id='2',boot_id='2'*32,session_id='3'*32))
    duplicate=b'{"v":1,"v":1}'
    covered=b'QWP1 '+str(len(duplicate)).encode()+b' '+duplicate
    duplicate=covered+f' {zlib.crc32(covered):08X}\n'.encode()
    def framed(payload):
        covered=b'QWP1 '+str(len(payload)).encode()+b' '+payload
        return covered+f' {zlib.crc32(covered):08X}\n'.encode()
    from polarimeter_controller import OfflineController,Timing,Readiness
    from polarimeter_protocol import FrameMessage,ProtocolError
    checker=OfflineController(timing=Timing(200000,200000,50000,20000,20000000),
        readiness=Readiness(.1,10000,5e6,20000),boot_id='1'*32)
    cases=[('hello',hello,True),('stop',stop,True),('crc-corrupt',stop[:-2]+b'Z\n',False),
           ('truncated',hello[:-1],False),('overlong',b'x'*1051,False),('duplicate-json-key',duplicate,True)]
    valid=dict(v=1,mode='SIMULATED',type='STOP',request_id='2',boot_id=None,session_id=None)
    cases.append(('bootstrap-stop',encode_frame(valid),True))
    for name,payload in [('escaped-duplicate',b'{"v":1,"\\u0076":1}'),('root-array',b'[]'),
            ('nan',b'{"n":NaN}'),('integer-overflow',b'{"n":9223372036854775808}'),
            ('nonfinite-float',b'{"n":1e999}'),('bad-utf8',b'{"n":"\xc0\xaf"}'),
            ('bad-surrogate',b'{"n":"\\ud800"}'),('deep',b'{"a":{"b":{"c":{"d":1}}}}'),
            ('valid-unicode',b'{"n":"\\ud83d\\ude80"}'),('empty-object',b'{}')]:
        cases.append((name,framed(payload),True))
    for name,change in [('unknown-field',{'extra':1}),('zero-rid',{'request_id':'0'}),
                        ('noncanonical-id',{'request_id':'02'}),('float-version',{'v':1.0}),
                        ('wrong-mode',{'mode':'HARDWARE'}),('bad-identity',{'boot_id':'abc'})]:
        cases.append((name,encode_frame(dict(valid,**change)),True))
    for key in ('v|mode','mode|type','request_id|boot_id','|v','v|'):
        cases.append(('delimiter-key-'+key,encode_frame(dict(valid,**{key:1})),True))
    payload=json.dumps(dict(valid,**{'v|mode':1}),separators=(',',':')).encode().replace(b'v|mode',b'v\\u007cmode')
    cases.append(('escaped-delimiter-key',framed(payload),True))
    rows=[]
    for name,frame,env in cases:
        host=schema=False
        try:
            event=FrameMessage(frame);host=True
            try:checker._schema(event);schema=True
            except ValueError:pass
        except (ValueError,ProtocolError):pass
        rows.append(dict(name=name,hex=frame.hex(),envelope_ok=env,host_json_ok=host,schema_ok=schema))
    return rows


def header(rows):
    lines=['#pragma once','#include <cstddef>','#include <cstdint>','namespace qwp {',
           'struct Vector {const std::uint8_t* data;std::size_t size;bool envelope_ok;bool schema_ok;};']
    for i,v in enumerate(rows):
        data=bytes.fromhex(v['hex'])
        lines.append(f'inline constexpr std::uint8_t vector_{i}[]={{'+','.join(str(x) for x in data)+'};')
    lines.append('inline constexpr Vector vectors[]={')
    for i,v in enumerate(rows):lines.append(f'{{vector_{i},sizeof(vector_{i}),'+str(v['envelope_ok']).lower()+','+str(v['schema_ok']).lower()+'},')
    return '\n'.join(lines+['};','} // namespace qwp',''])


if __name__=='__main__':
    rows=fixtures()
    (ROOT/'firmware/shared/vectors.json').write_text(json.dumps(rows,indent=2)+'\n')
    (ROOT/'firmware/shared/vectors.hpp').write_text(header(rows))
