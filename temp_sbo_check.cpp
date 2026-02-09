#include <functional>
#include <iostream>
#include <vector>
#include <cstdlib>

// ヒープ割り当てを検知するためのカスタムアロケータ（new/deleteのオーバーライドは影響範囲が広いため簡易的に）
void* operator new(std::size_t size) {
    std::cout << "Allocating " << size << " bytes" << std::endl;
    return std::malloc(size);
}

void operator delete(void* ptr) noexcept {
    std::cout << "Deallocating" << std::endl;
    std::free(ptr);
}

void operator delete(void* ptr, std::size_t) noexcept {
    std::cout << "Deallocating" << std::endl;
    std::free(ptr);
}

int main() {
    std::cout << "sizeof(std::function<void()>): " << sizeof(std::function<void()>) << std::endl;

    // 小さいラムダ
    std::cout << "--- Small Lambda ---" << std::endl;
    std::function<void()> f1 = [i = 0]() { };
    
    // 中くらいのラムダ
    std::cout << "--- Medium Lambda (capture 16 bytes) ---" << std::endl;
    long long a = 1, b = 2;
    std::function<void()> f2 = [a, b]() { };

    // 大きいラムダ
    std::cout << "--- Large Lambda (capture 64 bytes) ---" << std::endl;
    long long arr[8] = {0};
    std::function<void()> f3 = [arr]() { };

    return 0;
}
