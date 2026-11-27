#pragma once
#include <cmath>
#include <cstdint>
#include <deque>
#include <limits>
#include <optional>
#include <string>
#include "schema.hpp"
namespace qwp {
struct EncoderPolicy {
    double stationary_band,max_count_rate;
    std::uint64_t stationary_dwell,velocity_window;
};
struct RawSample {
    std::uint64_t time;
    std::int64_t count;
    std::string continuity;
    bool valid=true,has_count=true;
    bool operator==(const RawSample& other)const {
        return time==other.time&&count==other.count&&continuity==other.continuity
            &&valid==other.valid&&has_count==other.has_count;
    }
};
struct Reference {
    std::string epoch,calibration;
    std::int64_t count_zero;
    double angle_zero;
    int direction;
};
inline bool difference(std::int64_t a,std::int64_t b,std::int64_t& out){
    if((b>0&&a<std::numeric_limits<std::int64_t>::min()+b)
        ||(b<0&&a>std::numeric_limits<std::int64_t>::max()+b))return false;
    out=a-b;
return true;
}
struct Encoder final {
    explicit Encoder(EncoderPolicy policy):policy(policy){}
    EncoderPolicy policy;
    bool configured=false;
    double counts=0;
    std::string calibration;
    std::optional<RawSample> encoder,feedback;
    std::optional<Reference> reference;
    std::optional<double> velocity,angle;
    std::optional<std::uint64_t> stationary_since;
    std::deque<RawSample> window;
    std::deque<std::string> epochs;
    void invalidate(){reference.reset();
angle.reset();
    }
    void reset_samples(){encoder.reset();
feedback.reset();
velocity.reset();
angle.reset();
stationary_since.reset();
window.clear();
    }
    void clear(){invalidate();
reset_samples();
    }
    bool fresh(std::uint64_t now,std::uint64_t age,bool require_reference=false)const{
        return feedback&&feedback->valid&&feedback->has_count&&feedback->time<=now
            &&now-feedback->time<age&&velocity&&std::isfinite(*velocity)
            &&(!require_reference||(reference&&configured&&reference->calibration==calibration&&angle&&std::isfinite(*angle)));
    }
    bool stationary(std::uint64_t now,std::uint64_t age)const{
        return fresh(now,age)&&stationary_since&&feedback->time>=*stationary_since
            &&std::abs(*velocity)<=policy.stationary_band&&feedback->time-*stationary_since>=policy.stationary_dwell;
    }
    bool derive(const RawSample& sample,std::optional<double> speed){
        feedback=sample;
velocity=speed;
angle.reset();
        if(sample.valid&&sample.has_count&&reference&&configured&&reference->calibration==calibration){
            std::int64_t delta=0;
            if(!difference(sample.count,reference->count_zero,delta))return false;
            const long double a=reference->angle_zero+static_cast<long double>(reference->direction)*delta*360/counts;
            const auto value=static_cast<double>(a);
            if(!std::isfinite(value))return false;
            angle=value;
        }
        if(speed&&std::isfinite(*speed)&&std::abs(*speed)<=policy.stationary_band){
            if(!stationary_since)stationary_since=sample.time;
        }else stationary_since.reset();
        return true;
    }
    std::string ingest(const RawSample& sample,std::uint64_t now){
        if(!id(sample.continuity)||(sample.valid&&!sample.has_count)||sample.time>now
            ||(encoder&&(sample.time<encoder->time||(sample.time==encoder->time&&!(sample==*encoder))))){
            reset_samples();
return "FEEDBACK_INTEGRITY";
        }
        if(encoder&&sample==*encoder)return "";
        std::optional<double> speed;
        std::string cause;
        if(!sample.valid){window.clear();
invalidate();
cause="FEEDBACK";
        }
        else if(encoder&&encoder->valid){
            if(sample.continuity!=encoder->continuity){window.clear();
invalidate();
stationary_since.reset();
cause="FEEDBACK_INTEGRITY";
            }
            else{
                std::int64_t delta=0;
                const auto elapsed=sample.time-encoder->time;
                if(!difference(sample.count,encoder->count,delta)
                    ||std::abs(static_cast<long double>(delta))>policy.max_count_rate*(static_cast<long double>(elapsed)/1000000)+1){
                    invalidate();
cause="FEEDBACK_INTEGRITY";
                }else if(configured){
                    auto base=*encoder;
                    if(policy.velocity_window){
                        const auto earliest=sample.time>policy.velocity_window?sample.time-policy.velocity_window:0;
                        for(const auto& row:window)if(row.time>=earliest&&row.time<sample.time){base=row;
break;
                        }
                    }
                    std::int64_t window_delta=0;
                    const auto width=sample.time-base.time;
                    if(!width||!difference(sample.count,base.count,window_delta)){invalidate();
cause="FEEDBACK_INTEGRITY";
                    }
                    else{
                        const int sign=reference?reference->direction:1;
                        const long double v=static_cast<long double>(sign)*window_delta*360*1000000/(static_cast<long double>(counts)*width);
                        const double result=static_cast<double>(v);
                        if(!std::isfinite(result)){invalidate();
cause="FEEDBACK_INTEGRITY";
}else speed=result;
                    }
                }else if(delta==0)speed=0;
            }
        }
        encoder=sample;
        if(sample.valid){window.push_back(sample);
if(window.size()>128)window.pop_front();
        }
        if(!derive(sample,speed)){invalidate();
reset_samples();
return "FEEDBACK_INTEGRITY";
        }
        return cause;
    }
    void configure(double count_scale,const std::string& identity){
        if(!configured||identity!=calibration)invalidate();
        configured=true;
counts=count_scale;
calibration=identity;
    }
    bool establish(const Reference& candidate,std::uint64_t now,std::uint64_t age){
        if(!configured||!stationary(now,age)||!encoder||!id(candidate.epoch)||!id(candidate.calibration)
            ||candidate.calibration!=calibration||!std::isfinite(candidate.angle_zero)
            ||(candidate.direction!=1&&candidate.direction!=-1))return false;
        for(const auto& epoch:epochs)if(epoch==candidate.epoch)return false;
        std::int64_t delta=0;
        if(!difference(encoder->count,candidate.count_zero,delta))return false;
        const double value=static_cast<double>(candidate.angle_zero+static_cast<long double>(candidate.direction)*delta*360/counts);
        if(!std::isfinite(value))return false;
        epochs.push_back(candidate.epoch);
if(epochs.size()>32)epochs.pop_front();
        reference=candidate;
        return derive(*encoder,velocity);
    }
};
} // namespace qwp
