#pragma once
#include "contracts.hpp"
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
    const auto traces=controller_traces();
result.passed+=traces.passed;
result.failed+=traces.failed;
    if(!result.first_failure)result.first_failure=traces.first_failure;
    return result;
}
} // namespace qwp
