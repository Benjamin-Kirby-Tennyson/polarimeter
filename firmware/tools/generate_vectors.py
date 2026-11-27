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
    return [dict(name=name,hex=frame.hex(),envelope_ok=env,host_json_ok=host) for name,frame,env,host in (
        ('hello',hello,True,True),('stop',stop,True,True),
        ('crc-corrupt',stop[:-2]+b'Z\n',False,False),
        ('truncated',hello[:-1],False,False),('overlong',b'x'*1051,False,False),
        ('duplicate-json-key',duplicate,True,False))]


def header(rows):
    lines=['#pragma once','#include <cstddef>','#include <cstdint>','namespace qwp {',
           'struct Vector {const std::uint8_t* data;std::size_t size;bool envelope_ok;};']
    for i,v in enumerate(rows):
        data=bytes.fromhex(v['hex'])
        lines.append(f'inline constexpr std::uint8_t vector_{i}[]={{'+','.join(str(x) for x in data)+'};')
    lines.append('inline constexpr Vector vectors[]={')
    for i,v in enumerate(rows):lines.append(f'{{vector_{i},sizeof(vector_{i}),'+str(v['envelope_ok']).lower()+'},')
    return '\n'.join(lines+['};','} // namespace qwp',''])


if __name__=='__main__':
    rows=fixtures()
    (ROOT/'firmware/shared/vectors.json').write_text(json.dumps(rows,indent=2)+'\n')
    (ROOT/'firmware/shared/vectors.hpp').write_text(header(rows))
