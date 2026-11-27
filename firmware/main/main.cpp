// Compile-only mock image. No UART/GPIO/PWM/PCNT initialization or output.
#include "selftest.hpp"
#include "esp_timer.h"
namespace {
struct EspClock final:qwp::Clock {
    bool read(std::uint64_t& us)override{
        const auto value=esp_timer_get_time();
if(value<0)return false;
        us=static_cast<std::uint64_t>(value);
return true;
    }
};
}
// Distinguishable summary if later executed in an independently authorized
// emulator/debugger. A successful compile alone does not execute these tests.
extern "C" {
volatile unsigned qwp_selftest_passed=0;
volatile unsigned qwp_selftest_failed=0;
}
extern "C" void app_main(){
    EspClock clock;
qwp::UnconfiguredPcnt pcnt;
std::uint64_t now=0;
    const auto summary=qwp::selftest();
    qwp_selftest_passed=summary.passed;
    qwp_selftest_failed=summary.failed+(!clock.read(now)||pcnt.sample().valid?1u:0u);
    qwp::MockOutputs output;
output.inhibit();
}
