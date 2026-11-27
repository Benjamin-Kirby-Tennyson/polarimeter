// PROPOSED compile-only mock; never flashes/opens ports as part of this project.
#include "contracts.hpp"
#include "vectors.hpp"
#include "esp_timer.h"

namespace {
struct EspClock final:qwp::Clock {
    bool read(std::uint64_t& us) override {
        const auto value=esp_timer_get_time();
        if(value<0)return false;
        us=static_cast<std::uint64_t>(value);return true;
    }
};
}
extern "C" void app_main() {
    qwp::CompileOnlyTarget target;
    qwp::UnconfiguredPcnt pcnt;
    EspClock clock;
    std::uint64_t now=0;
    target.output.inhibit();
    if(!clock.read(now)||pcnt.sample().valid) target.fault=true;
    target.poll(now,50000); // Parser fixture timeout only, not a motor lease.
    for(const auto& v:qwp::vectors) {
        if(qwp::envelope(v.data,v.size)!=v.envelope_ok)target.fault=true;
        for(std::size_t pos=0;pos<v.size;) {
            const auto count=(v.size-pos<qwp::chunk_max)?v.size-pos:qwp::chunk_max;
            target.feed(v.data+pos,count,now,50000);pos+=count;
        }
        if(target.output.active)target.fault=true;
    }
    target.output.inhibit();
    // No tasks, UART, GPIO, PWM, flashing or motor-control implementation.
}
