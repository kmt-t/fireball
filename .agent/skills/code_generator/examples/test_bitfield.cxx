// Test compilation of generated bitfield struct
#include <cstdint>
#include <cstddef>

// Minimal definitions to make the generated code compile
namespace fireball {
    using operation_result = int;  // Placeholder
    template<typename T> struct list { T* data; size_t size; };
}

// Include generated header
#include "../inc/test/types.hxx"

int main() {
    using namespace fireball;
    
    // Test bitfield struct
    kv_pair kv{};
    kv.scope = 0xFF;
    kv.key = 0xABCDEF;
    kv.value = 0x12345678;
    
    // Verify size
    static_assert(sizeof(kv_pair) == 8, "kv_pair must be 64 bits");
    
    // Test that we can create and use the struct
    message msg{};
    
    return 0;
}
