#pragma once
#include "json.hpp"
#include <limits>
#include <initializer_list>
namespace qwp {
inline bool decimal64(std::string_view s,std::uint64_t& out,bool positive=false){
    if(s.empty()||(s.size()>1&&s[0]=='0'))return false;
    out=0;
for(const char c:s){if(c<'0'||c>'9'||out>(std::numeric_limits<std::uint64_t>::max()-static_cast<unsigned>(c-'0'))/10)return false;
out=out*10+c-'0';
}return !positive||out>0;
}
inline bool id(std::string_view s){if(s.size()!=32)return false;
for(const char c:s)if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')))return false;
return true;
}
struct Command {
    std::string type,raw,boot,session,nonce,config_id,calibration_id,challenge;
    std::uint64_t rid=0,deadline=0,generation=0,config_generation=0,heartbeat=0,duration=0;
    double target=0,speed=0,acceleration=0,minimum=0,maximum=0,counts=0,pstart=0,pend=0,pband=0;
};
inline bool admit(std::string_view payload,Command& c){
    Json j(payload);
if(!j.parse())return false;
    auto text=[&](const char* k,std::string& out){const auto* n=j.get(k);
if(!n||n->kind!=Kind::string)return false;
out=n->text;
return true;
};
    auto dec=[&](const char* k,std::uint64_t& out,bool positive=false){const auto* n=j.get(k);
return n&&n->kind==Kind::string&&decimal64(n->text,out,positive);
};
    auto numeric=[&](const char* k,double& out,int object=0){const auto* n=j.get(k,object);
if(!n||n->kind!=Kind::number)return false;
out=n->value;
return true;
};
    const auto* v=j.get("v");
if(!v||v->kind!=Kind::number||v->text!="1")return false;
    std::string mode;
if(!text("mode",mode)||mode!="SIMULATED"||!text("type",c.type)||!dec("request_id",c.rid,true))return false;
    std::string allowed="|v|mode|type|request_id|boot_id|session_id|";
    auto fields=[&](std::initializer_list<const char*> names){for(const auto* k:names){allowed+=k;
allowed+='|';
}};
    if(c.type=="HELLO"){
        fields({"host_nonce"});
const auto* b=j.get("boot_id");
const auto* s=j.get("session_id");
        if(!b||!s||b->kind!=Kind::null_value||s->kind!=Kind::null_value||!text("host_nonce",c.nonce)||!id(c.nonce))return false;
    }else{
        const auto* b=j.get("boot_id");
const auto* ss=j.get("session_id");
        if(c.type=="STOP"){
            if(!b||!ss)return false;
            if(b->kind!=Kind::null_value&&(!text("boot_id",c.boot)||!id(c.boot)))return false;
            if(ss->kind!=Kind::null_value&&(!text("session_id",c.session)||!id(c.session)))return false;
        }else if(!text("boot_id",c.boot)||!text("session_id",c.session)||!id(c.boot)||!id(c.session))return false;
        if(c.type!="STOP"&&c.type!="GET_STATUS"){fields({"not_after_us"});
if(!dec("not_after_us",c.deadline,true))return false;
        }
        if(c.type=="CONFIGURE"){
            fields({"config_generation","config"});
if(!dec("config_generation",c.config_generation))return false;
            const auto* object=j.get("config");
if(!object||object->kind!=Kind::object)return false;
const auto at=j.index(object);
            const auto* cfg=j.get("config_id",at);
const auto* cal=j.get("calibration_id",at);
            if(!cfg||!cal||cfg->kind!=Kind::string||cal->kind!=Kind::string||!id(cfg->text)||!id(cal->text))return false;
            c.config_id=cfg->text;
c.calibration_id=cal->text;
int n=0;
for(int i=object->first;
i>=0;
i=j.nodes[i].next)++n;
if(n!=7)return false;
            if(!numeric("counts_per_qwp_rev",c.counts,at)||!numeric("max_speed_deg_s",c.speed,at)||!numeric("max_acceleration_deg_s2",c.acceleration,at)||!numeric("min_angle_deg",c.minimum,at)||!numeric("max_angle_deg",c.maximum,at))return false;
            if(c.counts<=0||c.speed<=0||c.acceleration<=0||c.minimum>=c.maximum)return false;
        }else if(c.type=="ARM"){
            fields({"config_id","challenge"});
if(!text("config_id",c.config_id)||!id(c.config_id)||!text("challenge",c.challenge)||!id(c.challenge))return false;
        }else if(c.type=="HEARTBEAT"){
            fields({"arm_generation","heartbeat_seq"});
if(!dec("arm_generation",c.generation)||!dec("heartbeat_seq",c.heartbeat,true)||c.heartbeat!=c.rid)return false;
        }else if(c.type=="MOVE_TO"||c.type=="SCAN"){
            fields({"arm_generation","speed_deg_s","acceleration_deg_s2","duration_us"});
            const auto* target=c.type=="SCAN"?"end_deg":"target_deg";
fields({target});
            if(!dec("arm_generation",c.generation)||!dec("duration_us",c.duration,true)||!numeric(target,c.target)||!numeric("speed_deg_s",c.speed)||!numeric("acceleration_deg_s2",c.acceleration))return false;
            if(c.type=="SCAN"){fields({"plateau_start_deg","plateau_end_deg","plateau_speed_band_deg_s"});
if(!numeric("plateau_start_deg",c.pstart)||!numeric("plateau_end_deg",c.pend)||!numeric("plateau_speed_band_deg_s",c.pband))return false;
            }
        }else if(c.type!="STOP"&&c.type!="GET_STATUS"&&c.type!="DISARM"&&c.type!="CLEAR_FAULT")return false;
    }
    for(int i=j.nodes[0].first;
i>=0;
i=j.nodes[i].next){const auto& key=j.nodes[j.nodes[i].key].text;
if(allowed.find("|"+key+"|")==std::string::npos)return false;
    }
    c.raw=payload;
return true;
}
} // namespace qwp
