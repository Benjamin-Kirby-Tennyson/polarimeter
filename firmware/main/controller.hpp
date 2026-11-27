#pragma once
// Portable SIMULATED admission model. Feedback gates are explicit fixture inputs,
// not a PCNT/encoder implementation. MockOutputs can never energize hardware.
#include "contracts.hpp"
#include <algorithm>
#include <deque>
#include <set>
#include <vector>
namespace qwp {
struct Policy {std::uint64_t lease,request,feedback,cycle,motion;
};
struct Configuration {std::string id,calibration;
double counts,speed,acceleration,minimum,maximum;
};
struct Gates {bool fresh,stationary,inside,reference;
double angle;
};
struct Terminal {bool present=false;
std::string boot,session,outcome;
std::uint64_t arm=0,motion=0,time=0;
};
struct Cached {std::uint64_t rid;
std::string bytes,outcome;
};
struct Controller final {
    explicit Controller(Policy p):policy(p){}
    Policy policy;
    MockOutputs output;
    Configuration config{};
    std::string state="DISARMED",boot,session,challenge;
    std::set<std::string> faults;
    std::uint64_t generation=0,config_generation=0,request_id=0,hb=0,last=0,lease=0,end=0,challenge_end=0;
    std::uint64_t motion_id=0,next_identity=0;
    bool has_time=false,configured=false,referenced=false,logical_active=false,clean=true,tainted=false,exhausted=false;
    std::string hb_bytes;
    std::deque<Cached> cache;
    Terminal terminal,current_motion;
    std::vector<std::string> outcomes;
    bool add(std::uint64_t now,std::uint64_t delta,std::uint64_t& result)const{
        if(now>std::numeric_limits<std::uint64_t>::max()-delta)return false;
result=now+delta;
return true;
    }
    std::string fresh_id(){
        static constexpr char hex[]="0123456789abcdef";
std::string s(32,'0');
auto n=next_identity++;
        for(int i=31;
i>=0&&n;
--i){s[static_cast<std::size_t>(i)]=hex[n&15];
n>>=4;
}return s;
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
        inhibit();
state="FAULT";
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
        }
    }
    std::string dispatch(const Command& c,std::uint64_t now,const Gates& g){
        if(c.type=="GET_STATUS"){
            clean=true;
            if(challenge.empty()&&(state=="CONFIGURED"||state=="STOPPED")&&g.inside&&g.stationary&&referenced){challenge=fresh_id();
add(now,policy.request,challenge_end);
            }
        }else if(c.type=="CONFIGURE"){
            if((state!="DISARMED"&&state!="CONFIGURED"&&state!="STOPPED")||!g.stationary||c.config_generation!=config_generation)return "REJECTED";
            if(configured&&c.calibration_id==config.calibration&&c.counts!=config.counts)return "REJECTED";
            if(config_generation==std::numeric_limits<std::uint64_t>::max())return "REJECTED";
            inhibit();
if(!configured||c.calibration_id!=config.calibration)referenced=false;
            config={c.config_id,c.calibration_id,c.counts,c.speed,c.acceleration,c.minimum,c.maximum};
configured=true;
++config_generation;
state="CONFIGURED";
        }else if(c.type=="ARM"){
            if((state!="CONFIGURED"&&state!="STOPPED")||!faults.empty()||!g.inside||!g.stationary||!referenced||c.config_id!=config.id||challenge.empty()||c.challenge!=challenge||now>=challenge_end)return "REJECTED";
            if(generation==std::numeric_limits<std::uint64_t>::max()){fault("COUNTER_OVERFLOW");
return "REJECTED";
            }
            ++generation;
challenge.clear();
challenge_end=0;
add(now,policy.lease,lease);
state="ARMED";
        }else if(c.type=="MOVE_TO"||c.type=="SCAN"){
            if(state!="ARMED"||!g.inside||!referenced||c.generation!=generation)return "REJECTED";
            if(c.target<config.minimum||c.target>config.maximum||c.speed<=0||c.speed>config.speed||c.acceleration<=0||c.acceleration>config.acceleration||c.duration>policy.motion)return "REJECTED";
            if(c.type=="SCAN"){
                const int direction=c.target>g.angle?1:-1;
                if(c.target==g.angle||c.pband<=0||direction*(c.pstart-g.angle)<0||direction*(c.pstart-g.angle)>=direction*(c.pend-g.angle)||direction*(c.pend-g.angle)>direction*(c.target-g.angle))return "REJECTED";
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
            const std::set<std::string> permitted={"PROTOCOL","RX_OVERFLOW","TX_OVERFLOW","CONTROL_DEADLINE","LEASE_EXPIRED","MOTION_DURATION","EXTERNAL_INHIBIT","ID_CONFLICT","SEQUENCE_EXHAUSTED","FEEDBACK","TRAVEL_LIMIT"};
            if(state!="FAULT"||!g.inside||!g.stationary||!referenced)return "REJECTED";
            for(const auto& f:faults){if(!permitted.count(f)||(f=="PROTOCOL"&&!clean)||((f=="RX_OVERFLOW"||f=="TX_OVERFLOW")&&!clean)||(f=="ID_CONFLICT"&&tainted)||(f=="SEQUENCE_EXHAUSTED"&&exhausted)||f=="EXTERNAL_INHIBIT")return "REJECTED";
            }
            // Recovery predicates outside the tested projection stay inhibited.
            inhibit();
faults.clear();
state="DISARMED";
        }else return "REJECTED";
        return "ACCEPTED";
    }
    std::string request(const Command& c,std::uint64_t now,const Gates& g){
        if(!scope(c))return "WRONG_SESSION";
        for(const auto& old:cache)if(old.rid==c.rid&&old.bytes!=c.raw){fault("ID_CONFLICT");
return "ID_CONFLICT";
        }
        if(c.type!="GET_STATUS"&&!deadline(c.deadline,now,policy.request))return "EXPIRED_OR_FUTURE";
        for(const auto& old:cache)if(old.rid==c.rid)return "RETRY:"+old.outcome;
        if(c.rid<=request_id)return "STALE_ID";
request_id=c.rid;
        const auto result=dispatch(c,now,g);
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
    void cycle(std::uint64_t now,const std::vector<Command>& batch,const Gates& g,bool protocol_error=false,bool external=false,std::size_t tx_capacity=8){
        outcomes.clear();
output.inhibit();
        if(has_time&&now<last){fault("CLOCK");
settle_terminal(now,false);
return;
        }
        const bool armed=state=="ARMED"||state=="RUNNING";
        std::set<std::string> causes;
        if(external)causes.insert("EXTERNAL_INHIBIT");
        if(armed&&has_time&&now-last>policy.cycle)causes.insert("CONTROL_DEADLINE");
        std::uint64_t sum=0;
for(auto h:{policy.lease,policy.request,policy.motion})if(!add(now,h,sum))causes.insert("TIME_OVERFLOW");
        if(!challenge.empty()&&now>=challenge_end){challenge.clear();
challenge_end=0;
        }
        if(armed){if(!lease||now>=lease)causes.insert("LEASE_EXPIRED");
if(!g.fresh||!referenced)causes.insert("FEEDBACK");
if(end&&now>=end)causes.insert("MOTION_DURATION");
        }
        if(configured&&referenced&&!g.inside)causes.insert("TRAVEL_LIMIT");
        last=now;
has_time=true;
for(const auto& cause:causes)fault(cause);
        bool inhibited=!causes.empty();
        if(batch.size()>8){fault("RX_OVERFLOW");
settle_terminal(now,false);
return;
        }
        if(protocol_error){fault("PROTOCOL");
inhibited=true;
        }
        for(std::size_t i=0;
i<batch.size();
++i){const auto& c=batch[i];
            if(c.type=="HELLO"||c.type=="STOP"||!scope(c)||(c.type=="HEARTBEAT"&&c.generation!=generation))continue;
            if(c.rid==std::numeric_limits<std::uint64_t>::max()){fault("SEQUENCE_EXHAUSTED");
inhibited=true;
            }
            bool conflict=false;
            for(std::size_t j=0;
j<i;
++j)if((batch[j].type=="HEARTBEAT")== (c.type=="HEARTBEAT")&&batch[j].rid==c.rid&&scope(batch[j])&&batch[j].raw!=c.raw)conflict=true;
            if(c.type!="HEARTBEAT"){for(const auto& old:cache)if(old.rid==c.rid&&old.bytes!=c.raw)conflict=true;
            }
            else if(c.heartbeat==hb&&!hb_bytes.empty()&&hb_bytes!=c.raw)conflict=true;
            if(conflict){fault("ID_CONFLICT");
inhibited=true;
            }
        }
        if(generation==std::numeric_limits<std::uint64_t>::max()){fault("COUNTER_OVERFLOW");
inhibited=true;
        }
        if(batch.size()>tx_capacity){fault("TX_OVERFLOW");
inhibited=true;
        }
        for(const auto& c:batch)if(c.type=="STOP"){stop();
inhibited=true;
        }
        std::vector<std::pair<std::uint64_t,std::string>> disarms;
        if(!inhibited)for(const auto& c:batch)if(c.type=="DISARM"&&scope(c)){const auto result=request(c,now,g);
disarms.push_back({c.rid,result});
if(result=="ACCEPTED")inhibited=true;
        }
        int hellos=0;
for(const auto& c:batch)if(c.type=="HELLO")++hellos;
        bool hello_done=false;
        if(!inhibited&&hellos){inhibit();
referenced=false;
tainted=exhausted=false;
state=faults.empty()?"DISARMED":"FAULT";
session=fresh_id();
cache.clear();
request_id=0;
terminal={};
hello_done=true;
inhibited=true;
if(hellos>1)fault("IDENTITY");
        }
        int changes=0;
for(const auto& c:batch)if(c.type=="CONFIGURE"||c.type=="ARM"||c.type=="MOVE_TO"||c.type=="SCAN"||c.type=="CLEAR_FAULT")++changes;
        for(const auto& c:batch){
            std::string result;
            auto disarm=std::find_if(disarms.begin(),disarms.end(),[&](const auto& p){return p.first==c.rid;
});
            if(c.type=="STOP")result="INHIBIT_REQUESTED";
            else if(c.type=="HELLO"&&hello_done)result=hellos==1?"HELLO_REPLY":"REJECTED";
            else if(c.type=="DISARM"&&disarm!=disarms.end())result=disarm->second;
            else if(inhibited)result=c.type=="GET_STATUS"&&scope(c)?"STATUS":"BATCH_INHIBITED";
            else if(changes>1&&(c.type=="CONFIGURE"||c.type=="ARM"||c.type=="MOVE_TO"||c.type=="SCAN"||c.type=="CLEAR_FAULT"))result="MULTIPLE_STATE_CHANGES";
            else if(c.type=="HEARTBEAT")result=heartbeat(c,now);
            else result=request(c,now,g);
            outcomes.push_back(result);
inhibited=inhibited||!faults.empty();
        }
        if(batch.size()>tx_capacity)outcomes.clear();
settle_terminal(now,false);
output.inhibit();
    }
    void complete(std::uint64_t now){if(state=="RUNNING"&&faults.empty()){stop();
settle_terminal(now,true);
    }}
};
} // namespace qwp
