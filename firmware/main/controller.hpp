#pragma once
// Portable in-memory controller; derived encoder/reference state, never live I/O.
#include "contracts.hpp"
#include "encoder.hpp"
#include <algorithm>
#include <functional>
#include <set>
#include <vector>
namespace qwp {
struct Policy {std::uint64_t lease,request,feedback,cycle,motion;
};
struct Configuration {std::string id,calibration;
double counts=0,speed=0,acceleration=0,minimum=0,maximum=0;
};
struct Terminal {bool present=false;
std::string boot,session,outcome;
std::uint64_t arm=0,motion=0,time=0;
};
struct Cached {std::uint64_t rid;
std::string bytes,outcome;
};
struct Controller final {
    Controller(Policy p,EncoderPolicy readiness,std::string boot_identity,std::uint64_t identity_seed)
        :policy(p),encoder(readiness),boot(std::move(boot_identity)),next_identity(identity_seed){
        recent_ids.push_back(boot);
        if(!id(boot)||!p.lease||!p.request||!p.feedback||!p.cycle||!p.motion
            ||!std::isfinite(readiness.stationary_band)||readiness.stationary_band<0
            ||!std::isfinite(readiness.max_count_rate)||readiness.max_count_rate<=0)fault("IDENTITY");
    }
    Policy policy;
    Encoder encoder;
    MockOutputs output;
    Configuration config{};
    std::string state="DISARMED",boot,session,challenge;
    std::set<std::string> faults;
    std::uint64_t generation=0,config_generation=0,request_id=0,hb=0,last=0,lease=0,end=0,challenge_end=0;
    std::uint64_t motion_id=0,next_identity;
    bool has_time=false,configured=false,logical_active=false,clean=true,tainted=false,exhausted=false;
    bool external=false,cycle_healthy=true,admission_ok=true,stall_released=false;
    std::string hb_bytes;
    std::deque<Cached> cache;
    std::deque<std::string> recent_ids;
    Terminal terminal,current_motion;
    std::vector<std::string> outcomes;
    bool add(std::uint64_t now,std::uint64_t delta,std::uint64_t& result)const{
        if(now>std::numeric_limits<std::uint64_t>::max()-delta)return false;
        result=now+delta;
return true;
    }
    std::string fresh_id(){
        if(next_identity==std::numeric_limits<std::uint64_t>::max()){fault("IDENTITY");
return "";
        }
        static constexpr char hex[]="0123456789abcdef";
        std::string s(32,'0');
auto n=next_identity++;
        for(int i=31;
i>=0&&n;
--i){s[static_cast<std::size_t>(i)]=hex[n&15];
n>>=4;
        }
        for(const auto& previous:recent_ids)if(s==previous){fault("IDENTITY");
return "";
        }
        recent_ids.push_back(s);
if(recent_ids.size()>32)recent_ids.pop_front();
return s;
    }
    void invalidate_reference(){encoder.invalidate();
challenge.clear();
challenge_end=0;
    }
    void inhibit(){
        output.inhibit();
logical_active=false;
lease=end=0;
challenge.clear();
challenge_end=0;
motion_id=0;
hb=0;
hb_bytes.clear();
        if(generation==std::numeric_limits<std::uint64_t>::max())faults.insert("COUNTER_OVERFLOW");
else ++generation;
    }
    void fault(const std::string& cause){
        faults.insert(cause);
        if(cause=="PROTOCOL"||cause=="RX_OVERFLOW"||cause=="TX_OVERFLOW")clean=false;
        if(cause=="ID_CONFLICT")tainted=true;
        if(cause=="SEQUENCE_EXHAUSTED")exhausted=true;
        if(cause=="STALL")stall_released=false;
        if(cause=="FEEDBACK"||cause=="FEEDBACK_INTEGRITY"||cause=="CLOCK"||cause=="TIME_OVERFLOW"){
            invalidate_reference();
encoder.stationary_since.reset();
encoder.window.clear();
        }
        if(cause=="CLOCK"||cause=="TIME_OVERFLOW"){encoder.feedback.reset();
encoder.velocity.reset();
        }
        inhibit();
state="FAULT";
    }
    bool fresh(bool reference=false)const{return has_time&&encoder.fresh(last,policy.feedback,reference);
    }
    bool inside()const{return fresh(true)&&*encoder.angle>=config.minimum&&*encoder.angle<=config.maximum;
    }
    bool stationary()const{return has_time&&encoder.stationary(last,policy.feedback);
    }
    bool establish_reference(const Reference& value){
        if(logical_active||!configured||(state!="DISARMED"&&state!="CONFIGURED"&&state!="STOPPED"&&state!="FAULT")
            ||faults.count("TRAVEL_LIMIT")||faults.count("TRAVEL_PREDICTED"))return false;
        if(!encoder.establish(value,last,policy.feedback))return false;
        challenge.clear();
challenge_end=0;
return true;
    }
    void stop(){inhibit();
state=faults.empty()?"STOPPED":"FAULT";
    }
    bool scope(const Command& c)const{return !session.empty()&&c.boot==boot&&c.session==session;
    }
    bool deadline(std::uint64_t d,std::uint64_t now,std::uint64_t horizon)const{
        std::uint64_t upper=0;
return add(now,horizon,upper)&&d>now&&d<=upper;
    }
    void settle_terminal(std::uint64_t now,bool completed){
        if(current_motion.present&&state!="RUNNING"){
            terminal=current_motion;
terminal.time=now;
terminal.outcome=state=="FAULT"?"FAULT":completed?"COMPLETED":"CANCELLED";
current_motion={};
            if(terminal.session!=session)terminal={};
        }
    }
    bool recoverable(const std::string& f)const{
        if(f=="FEEDBACK"||f=="FEEDBACK_INTEGRITY"||f=="TRAVEL_LIMIT"||f=="TRAVEL_PREDICTED")return inside();
        if(f=="STALL")return inside()&&stall_released;
        if(f=="EXTERNAL_INHIBIT")return !external;
        if(f=="CONTROL_DEADLINE")return cycle_healthy;
        if(f=="PROTOCOL")return clean;
        if(f=="RX_OVERFLOW"||f=="TX_OVERFLOW")return admission_ok&&clean;
        if(f=="ID_CONFLICT")return !tainted;
        if(f=="SEQUENCE_EXHAUSTED")return !exhausted;
        if(f=="LEASE_EXPIRED")return lease==0;
        if(f=="MOTION_DURATION")return end==0;
        return false;
    }
    std::string dispatch(const Command& c,std::uint64_t now){
        if(c.type=="GET_STATUS"){
            clean=true;
            if(challenge.empty()&&(state=="CONFIGURED"||state=="STOPPED")&&inside()&&stationary()){
                challenge=fresh_id();
if(challenge.empty())return "REJECTED";
add(now,policy.request,challenge_end);
            }
        }else if(c.type=="CONFIGURE"){
            if((state!="DISARMED"&&state!="CONFIGURED"&&state!="STOPPED")||!stationary()||c.config_generation!=config_generation)return "REJECTED";
            if(configured&&c.calibration_id==config.calibration&&c.counts!=config.counts)return "REJECTED";
            if(config_generation==std::numeric_limits<std::uint64_t>::max())return "REJECTED";
            inhibit();
            config={c.config_id,c.calibration_id,c.counts,c.speed,c.acceleration,c.minimum,c.maximum};
            encoder.configure(c.counts,c.calibration_id);
configured=true;
++config_generation;
state="CONFIGURED";
        }else if(c.type=="ARM"){
            if((state!="CONFIGURED"&&state!="STOPPED")||!faults.empty()||!fresh(true)||!inside()||!stationary()
                ||c.config_id!=config.id||challenge.empty()||c.challenge!=challenge||now>=challenge_end)return "REJECTED";
            if(generation==std::numeric_limits<std::uint64_t>::max()){fault("COUNTER_OVERFLOW");
return "REJECTED";
            }
            ++generation;
challenge.clear();
challenge_end=0;
add(now,policy.lease,lease);
state="ARMED";
        }else if(c.type=="MOVE_TO"||c.type=="SCAN"){
            if(state!="ARMED"||!fresh(true)||!inside()||c.generation!=generation)return "REJECTED";
            if(c.target<config.minimum||c.target>config.maximum||c.speed<=0||c.speed>config.speed||c.acceleration<=0
                ||c.acceleration>config.acceleration||c.duration>policy.motion)return "REJECTED";
            if(c.type=="SCAN"){
                const double angle=*encoder.angle;
const int direction=c.target>angle?1:-1;
                if(c.target==angle||c.pband<=0||direction*(c.pstart-angle)<0||direction*(c.pstart-angle)>=direction*(c.pend-angle)
                    ||direction*(c.pend-angle)>direction*(c.target-angle))return "REJECTED";
            }
            if(!add(now,c.duration,end))return "REJECTED";
            motion_id=c.rid;
state="RUNNING";
logical_active=true;
            current_motion={true,boot,session,"",generation,motion_id,now};
        }else if(c.type=="DISARM"){inhibit();
state=faults.empty()?"DISARMED":"FAULT";
        }
        else if(c.type=="CLEAR_FAULT"){
            if(state!="FAULT"||!inside()||!stationary())return "REJECTED";
            for(const auto& f:faults)if(!recoverable(f))return "REJECTED";
            inhibit();
faults.clear();
state="DISARMED";
        }else return "REJECTED";
        return "ACCEPTED";
    }
    std::string request(const Command& c,std::uint64_t now){
        if(!scope(c))return "WRONG_SESSION";
        for(const auto& old:cache)if(old.rid==c.rid&&old.bytes!=c.raw){fault("ID_CONFLICT");
return "ID_CONFLICT";
        }
        if(c.type!="GET_STATUS"&&!deadline(c.deadline,now,policy.request))return "EXPIRED_OR_FUTURE";
        for(const auto& old:cache)if(old.rid==c.rid)return "RETRY:"+old.outcome;
        if(c.rid<=request_id)return "STALE_ID";
        request_id=c.rid;
const auto result=dispatch(c,now);
cache.push_back({c.rid,c.raw,result});
if(cache.size()>8)cache.pop_front();
return result;
    }
    std::string heartbeat(const Command& c,std::uint64_t now){
        if(!scope(c)||(state!="ARMED"&&state!="RUNNING")||c.generation!=generation)return "WRONG_ARM_OR_SESSION";
        if(c.heartbeat==hb&&hb_bytes!=c.raw){fault("ID_CONFLICT");
return "ID_CONFLICT";
        }
        if(c.heartbeat<=hb)return "STALE_HEARTBEAT";
        if(!deadline(c.deadline,now,policy.lease))return "EXPIRED_OR_FUTURE";
        hb=c.heartbeat;
hb_bytes=c.raw;
lease=c.deadline;
return "ACCEPTED";
    }
    void cycle(std::uint64_t now,const std::vector<Command>& batch,const std::optional<RawSample>& sample,
        bool protocol_error=false,bool external_inhibit=false,std::size_t tx_capacity=8,bool rx_overflow=false,std::size_t error_events=0){
        outcomes.clear();
output.inhibit();
        if(has_time&&now<last){fault("CLOCK");
settle_terminal(now,false);
return;
        }
        const bool armed=state=="ARMED"||state=="RUNNING";
        cycle_healthy=!has_time||now-last<=policy.cycle;
        external=external_inhibit;
        const auto event_count=(batch.size()>8||error_events>8)?std::size_t(9):batch.size()+error_events;
        if(tx_capacity>8){fault("TX_OVERFLOW");
settle_terminal(now,false);
return;
        }
        admission_ok=event_count<=8&&!rx_overflow&&event_count<=tx_capacity;
        last=now;
has_time=true;
        std::set<std::string> causes;
        if(external)causes.insert("EXTERNAL_INHIBIT");
        if(armed&&!cycle_healthy)causes.insert("CONTROL_DEADLINE");
        if(sample){const auto cause=encoder.ingest(*sample,now);
if(!cause.empty())causes.insert(cause);
        }
        if(encoder.reference&&!fresh(true)){invalidate_reference();
encoder.stationary_since.reset();
encoder.window.clear();
        }
        std::uint64_t sum=0;
for(auto h:{policy.lease,policy.request,policy.motion})if(!add(now,h,sum))causes.insert("TIME_OVERFLOW");
        if(!challenge.empty()&&now>=challenge_end){challenge.clear();
challenge_end=0;
        }
        if(armed){if(!lease||now>=lease)causes.insert("LEASE_EXPIRED");
if(!fresh(true))causes.insert("FEEDBACK");
if(end&&now>=end)causes.insert("MOTION_DURATION");
        }
        if(fresh(true)&&!inside())causes.insert("TRAVEL_LIMIT");
        for(const auto& cause:causes)fault(cause);
        bool inhibited=!causes.empty();
        if(event_count>8||rx_overflow){fault("RX_OVERFLOW");
settle_terminal(now,false);
return;
        }
        if(protocol_error){fault("PROTOCOL");
inhibited=true;
        }
        // Snapshot eligible events before mutation; STOP/HELLO and stale-arm HB
        // are excluded on BOTH sides of exact-byte ID conflict comparison.
        std::vector<const Command*> eligible;
        for(const auto& c:batch){
            if(c.type=="HELLO"||c.type=="STOP"||!scope(c)||(c.type=="HEARTBEAT"&&c.generation!=generation))continue;
            if(c.rid==std::numeric_limits<std::uint64_t>::max()){fault("SEQUENCE_EXHAUSTED");
inhibited=true;
            }
            bool conflict=false;
            for(const auto* old:eligible)if((old->type=="HEARTBEAT")== (c.type=="HEARTBEAT")&&old->rid==c.rid&&old->raw!=c.raw)conflict=true;
            if(c.type!="HEARTBEAT"){for(const auto& old:cache)if(old.rid==c.rid&&old.bytes!=c.raw)conflict=true;
            }
            else if(c.heartbeat==hb&&!hb_bytes.empty()&&hb_bytes!=c.raw)conflict=true;
            if(conflict){fault("ID_CONFLICT");
inhibited=true;
            }
            eligible.push_back(&c);
        }
        if(generation==std::numeric_limits<std::uint64_t>::max()){fault("COUNTER_OVERFLOW");
inhibited=true;
        }
        if(event_count>tx_capacity){fault("TX_OVERFLOW");
inhibited=true;
        }
        for(const auto& c:batch)if(c.type=="STOP"){stop();
inhibited=true;
        }
        std::vector<std::optional<std::string>> disarms(batch.size());
        if(!inhibited)for(std::size_t i=0;
i<batch.size();
        ++i)if(batch[i].type=="DISARM"&&scope(batch[i])){
            disarms[i]=request(batch[i],now);
if(*disarms[i]=="ACCEPTED")inhibited=true;
        }
        int hellos=0;
for(const auto& c:batch)if(c.type=="HELLO")++hellos;
        std::optional<std::string> hello_outcome;
        if(!inhibited&&hellos){
            if(hellos!=1){fault("IDENTITY");
hello_outcome="REJECTED";
            }
            else{
                inhibit();
encoder.clear();
tainted=exhausted=false;
state=faults.empty()?"DISARMED":"FAULT";
                const auto candidate=fresh_id();
                if(candidate.empty()){fault("IDENTITY");hello_outcome="REJECTED";}
                else{session=candidate;
cache.clear();
request_id=0;
terminal={};
hello_outcome="HELLO_REPLY";
                }
            }
            inhibited=true;
        }
        int changes=0;
for(const auto& c:batch)if(c.type=="CONFIGURE"||c.type=="ARM"||c.type=="MOVE_TO"||c.type=="SCAN"||c.type=="CLEAR_FAULT")++changes;
        for(std::size_t i=0;
i<batch.size();
        ++i){
            const auto& c=batch[i];
std::string result;
            if(c.type=="STOP")result="INHIBIT_REQUESTED";
            else if(c.type=="HELLO"&&hello_outcome)result=*hello_outcome;
            else if(disarms[i])result=*disarms[i];
            else if(inhibited)result=c.type=="GET_STATUS"&&scope(c)?"STATUS":"BATCH_INHIBITED";
            else if(changes>1&&(c.type=="CONFIGURE"||c.type=="ARM"||c.type=="MOVE_TO"||c.type=="SCAN"||c.type=="CLEAR_FAULT"))result="MULTIPLE_STATE_CHANGES";
            else if(c.type=="HEARTBEAT")result=heartbeat(c,now);
            else result=request(c,now);
            outcomes.push_back(result);
inhibited=inhibited||!faults.empty();
        }
        if(event_count>tx_capacity)outcomes.clear();
        settle_terminal(now,false);
output.inhibit();
    }
    // Explicit oracle test hook only; NOT encoder-measured settling/completion.
    void complete_fixture_only(std::uint64_t now){if(state=="RUNNING"&&faults.empty()){stop();
settle_terminal(now,true);
    }}
};
} // namespace qwp
