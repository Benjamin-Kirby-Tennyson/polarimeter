// Run only on a separate build computer; never built/executed on this Pi.
#include "../main/contracts.hpp"
#include "../shared/vectors.hpp"
#include <cassert>
#include <cstring>
int main() {
    const auto* check=reinterpret_cast<const std::uint8_t*>("123456789");
    assert(qwp::crc32(check,9)==0xcbf43926u);
    for(const auto& v:qwp::vectors) {
        assert(qwp::envelope(v.data,v.size)==v.envelope_ok);
        qwp::CompileOnlyTarget target;
        for(std::size_t i=0;i<v.size;++i)target.feed(v.data+i,1,i,50000);
        assert(!target.output.active);
        assert(!target.output.request_enable());
    }
    qwp::CompileOnlyTarget t;
    const auto* partial=reinterpret_cast<const std::uint8_t*>("QWP1 ");
    t.feed(partial,5,0,50000);t.poll(50000,50000);
    assert(t.fault && t.discard && !t.output.active);
    t.poll(1,50000);assert(t.fault && !t.output.active);
    return 0;
}
