#pragma once
// PROPOSED, NOT TARGET-COMPILED. No UART/GPIO/PCNT driver is initialized.
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace qwp {
constexpr std::size_t payload_max=1024, frame_max=1050, chunk_max=256;
constexpr std::size_t json_depth_max=4, json_nodes_max=128;
inline std::uint32_t crc32(const std::uint8_t* data,std::size_t n) {
    std::uint32_t crc=0xffffffffu;
    for(std::size_t i=0;i<n;++i) {
        crc^=data[i];
        for(int bit=0;bit<8;++bit) crc=(crc>>1)^((crc&1)?0xedb88320u:0u);
    }
    return crc^0xffffffffu;
}
// Envelope ONLY: payload is intentionally untrusted, with no JSON decoder or
// command admission path. JSON strictness/state conformance remains a blocker.
inline bool envelope(const std::uint8_t* data,std::size_t size) {
    if(size<18 || size>frame_max || std::memcmp(data,"QWP1 ",5)!=0 || data[size-1]!='\n') return false;
    for(std::size_t i=0;i+1<size;++i) if(data[i]=='\n'||data[i]=='\r') return false;
    std::size_t pos=5,length=0,digits=0;
    if(data[pos]<'1'||data[pos]>'9') return false;
    while(pos<size && data[pos]>='0' && data[pos]<='9') {
        if(++digits>4) return false;
        length=length*10+data[pos++]-'0';
    }
    if(pos>=size || data[pos++]!=' ' || length>payload_max || length==0) return false;
    const auto end=pos+length;
    if(end+10!=size || data[end]!=' ') return false;
    std::uint32_t expected=0;
    for(std::size_t i=end+1;i<end+9;++i) {
        const auto c=data[i];
        if(!((c>='0'&&c<='9')||(c>='A'&&c<='F'))) return false;
        expected=(expected<<4)|(c<='9'?c-'0':c-'A'+10);
    }
    return expected==crc32(data,end);
}
struct Clock {
    virtual ~Clock()=default;
    virtual bool read(std::uint64_t& us)=0;
};
struct EncoderObservation { std::int64_t count=0; std::uint64_t time_us=0; bool valid=false; };
struct PcntSource {
    virtual ~PcntSource()=default;
    virtual EncoderObservation sample()=0;
};
// Deliberately unavailable. Encoder pins, levels, xN decoding and scale unset.
struct UnconfiguredPcnt final:PcntSource {
    EncoderObservation sample() override { return {}; }
};
struct MockOutputs final {
    bool active=false;
    void inhibit() {active=false;}
    bool request_enable() {inhibit();return false;}
};
struct CompileOnlyTarget final {
    MockOutputs output;
    std::array<std::uint8_t,frame_max> buffer{};
    std::size_t used=0;
    std::uint64_t last=0,start=0;
    bool seen_clock=false,partial=false,discard=false,fault=false;
    void reject() {fault=true;output.inhibit();used=0;partial=false;discard=true;}
    void poll(std::uint64_t now,std::uint64_t partial_timeout_us) {
        if(partial_timeout_us==0 || (seen_clock && now<last)) {reject();return;}
        last=now;seen_clock=true;
        if(partial && now-start>=partial_timeout_us) reject();
        output.inhibit();
    }
    void feed(const std::uint8_t* data,std::size_t n,std::uint64_t now,std::uint64_t timeout) {
        poll(now,timeout);
        if(n>chunk_max) {reject();return;}
        for(std::size_t i=0;i<n;++i) {
            const auto c=data[i];
            if(discard) {if(c=='\n')discard=false;continue;}
            if(!partial) {partial=true;start=now;}
            if(used==frame_max) {reject();continue;}
            buffer[used++]=c;
            if(c=='\n') {
                // Even a valid envelope cannot authorize a command here.
                if(!envelope(buffer.data(),used)) fault=true;
                output.inhibit();used=0;partial=false;
            } else if(c=='\r'||used==frame_max) reject();
        }
    }
};
} // namespace qwp
