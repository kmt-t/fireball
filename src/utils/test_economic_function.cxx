#include "utils/economic_function.hxx"
#include <iostream>
#include <cassert>

using namespace fireball::utils;

int main() {
    // 1. Capture-less lambda
    economic_function<int(int)> f1 = [](int x) { return x * 2; };
    assert(f1(5) == 10);
    std::cout << "Test 1 passed: Capture-less lambda" << std::endl;

    // 2. Small capture (fits in 16 bytes)
    int a = 10;
    economic_function<int(int)> f2 = [a](int x) { return x + a; };
    assert(f2(5) == 15);
    std::cout << "Test 2 passed: Small capture" << std::endl;

    // 3. Move construction
    economic_function<int(int)> f3 = std::move(f2);
    assert(f3(10) == 20);
    assert(!f2);
    std::cout << "Test 3 passed: Move construction" << std::endl;

    std::cout << "All basic economic_function tests passed!" << std::endl;
    return 0;
}
