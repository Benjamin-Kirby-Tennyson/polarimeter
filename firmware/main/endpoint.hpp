#pragma once
#include "controller.hpp"
namespace qwp {
struct MemoryEndpoint final {
    MemoryEndpoint(Policy p,EncoderPolicy readiness,std::string boot,std::uint64_t seed,std::uint64_t timeout)
        :controller(p,readiness,std::move(boot),seed),partial_timeout(timeout){}
    CompileOnlyTarget parser;
    Controller controller;
    const std::uint64_t partial_timeout;
    void feed(const std::uint8_t* bytes,std::size_t size,std::uint64_t now){parser.feed(bytes,size,now,partial_timeout);
    }
    void cycle(std::uint64_t now,const std::optional<RawSample>& sample,bool external=false,std::size_t capacity=8){
        parser.poll(now,partial_timeout);
        std::vector<Command> commands;
commands.reserve(8);
std::size_t errors=0;
        for(std::size_t i=0;
i<parser.event_count;
        ++i){
            const auto& event=parser.events[i];
if(event.error)++errors;
else commands.push_back(event.command);
        }
        controller.cycle(now,commands,sample,errors>0||parser.fatal_timing,external,capacity,parser.overflow,errors);
        parser.clear_events();
    }
};
} // namespace qwp
