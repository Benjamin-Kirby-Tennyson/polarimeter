// Execute only on a separate build machine/hosted CI, never compile on the Pi.
#include "../main/selftest.hpp"
#include <iostream>
int main(){
    const auto result=qwp::selftest();
    std::cout<<"SIMULATED native conformance: passed="<<result.passed<<" failed="<<result.failed<<'\n';
    if(result.first_failure)std::cerr<<"first failure: "<<result.first_failure<<'\n';
    return result.failed?1:0;
}
