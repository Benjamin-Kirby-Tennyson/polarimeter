#pragma once
#include "contracts.hpp"
#include "endpoint.hpp"
#include "../shared/vectors.hpp"
#include "../shared/controller-traces.hpp"
namespace qwp {
struct SelfTestResult {unsigned passed=0,failed=0;
const char* first_failure=nullptr;
};
inline SelfTestResult selftest(){
    SelfTestResult result;
    auto check=[&](bool success,const char* label){if(success)++result.passed;
else{++result.failed;
if(!result.first_failure)result.first_failure=label;
}};
    for(const auto& v:vectors){
        check(envelope(v.data,v.size)==v.envelope_ok,"envelope");
        for(const auto width:{std::size_t(1),std::size_t(7),chunk_max}){
            CompileOnlyTarget target;
            for(std::size_t pos=0;
pos<v.size;
){const auto n=std::min(width,v.size-pos);
target.feed(v.data+pos,n,pos,50000);
pos+=n;
            }
            target.poll(v.size+50000,50000);
            check(target.accepted==(v.schema_ok?1u:0u),"schema acceptance");
            check(target.errors==(v.schema_ok?0u:1u),"parser errors");
            check(target.fault==!v.schema_ok,"parser fault");
            check(!target.output.active&&!target.output.request_enable(),"output inhibition");
        }
    }
    {
        CompileOnlyTarget t;
const std::uint8_t partial[]={'Q','W','P','1',' '};
        t.feed(partial,sizeof(partial),0,50000);
t.poll(49999,50000);
check(!t.fault,"before timeout");
        t.poll(50000,50000);
check(t.errors==1&&t.discard,"timeout equality");
        const std::uint8_t lf='\n';
t.feed(&lf,1,50001,50000);
        t.feed(vector_0,sizeof(vector_0),50002,50000);
check(t.accepted==1&&t.errors==1&&!t.discard,"LF resync");
    }
    {
        CompileOnlyTarget t;
std::array<std::uint8_t,257> chunk{};
        t.feed(chunk.data(),chunk.size(),100,50000);
check(t.errors==1&&t.accepted==0,"chunk overflow");
        t.feed(vector_0,sizeof(vector_0),99,50000);
check(t.errors==2&&t.accepted==0,"backwards clock");
    }
    {
        Command value;
        check(decode(vector_0,sizeof(vector_0),value)&&!value.type.empty(),"command initial admission");
        const std::uint8_t invalid[]={'x'};
        check(!decode(invalid,sizeof(invalid),value)&&value.type.empty()&&value.raw.empty(),"atomic failure reset");
    }
    {
        CompileOnlyTarget parser;
        const std::uint8_t lf='\n';
        parser.feed(&lf,1,0,0);
        parser.feed(vector_0,sizeof(vector_0),1,50000);
        check(parser.fatal_timing&&parser.accepted==0,"invalid timing is latched across LF");
    }
    {
        MemoryEndpoint endpoint(Policy{200000,200000,50000,20000,20000000},EncoderPolicy{.1,5e6,10000,20000},"11111111111111111111111111111111",100,50000);
        std::vector<std::uint8_t> bytes(vector_0,vector_0+sizeof(vector_0));
        bytes.push_back('x');
bytes.push_back('\n');
        bytes.insert(bytes.end(),vector_1,vector_1+sizeof(vector_1));
        for(std::size_t i=0;
i<bytes.size();
){const auto n=std::min(chunk_max,bytes.size()-i);
endpoint.feed(bytes.data()+i,n,0);
i+=n;
        }
        check(endpoint.parser.event_count==3&&!endpoint.parser.events[0].error&&endpoint.parser.events[1].error&&endpoint.parser.events[2].command.type=="STOP","ordered frame error STOP events");
        endpoint.cycle(0,RawSample{0,0,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",true,true});
        check(endpoint.controller.state=="FAULT"&&endpoint.controller.faults.count("PROTOCOL")&&endpoint.controller.outcomes==std::vector<std::string>{"BATCH_INHIBITED","INHIBIT_REQUESTED"},"endpoint preflight fault STOP dominance");
        check(!endpoint.controller.logical_active&&!endpoint.controller.output.active,"endpoint output remains inhibited");
    }
    {
        MemoryEndpoint endpoint(Policy{200000,200000,50000,20000,20000000},EncoderPolicy{.1,5e6,10000,20000},"11111111111111111111111111111111",100,50000);
        for(int i=0;
i<9;
++i)endpoint.feed(vector_0,sizeof(vector_0),0);
        check(endpoint.parser.overflow&&endpoint.parser.event_count==8,"bounded ordered queue overflow");
        endpoint.cycle(0,RawSample{0,0,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",true,true});
        check(endpoint.controller.faults.count("RX_OVERFLOW")&&!endpoint.controller.logical_active&&endpoint.controller.outcomes.empty(),"overflow inhibits before any HELLO mutation");
    }
    {
        std::int64_t delta=0;
        check(!difference(std::numeric_limits<std::int64_t>::max(),std::numeric_limits<std::int64_t>::min(),delta),"signed count overflow");
        check(difference(-10,10,delta)&&delta==-20,"signed count difference");
    }
    const auto traces=controller_traces();
result.passed+=traces.passed;
result.failed+=traces.failed;
    if(!result.first_failure)result.first_failure=traces.first_failure;
    return result;
}
} // namespace qwp
