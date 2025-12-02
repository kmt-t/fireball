#include "coos/co_mem.hxx"
#include <cstring> // for size_t

// External declarations for dlmalloc mspace functions
extern "C" {
    using mspace = void*;
    mspace create_mspace(size_t capacity, int locked);
    size_t destroy_mspace(mspace msp);
    void* mspace_malloc(mspace msp, size_t bytes);
    void mspace_free(mspace msp, void* mem);
    void* mspace_realloc(mspace msp, void* mem, size_t newsize);
    
    struct mallinfo {
        size_t arena;    /* non-mmapped space allocated from system */
        size_t ordblks;  /* number of free chunks */
        size_t smblks;   /* always 0 */
        size_t hblks;    /* always 0 */
        size_t hblkhd;   /* space in mmapped regions */
        size_t usmblks;  /* maximum total allocated space */
        size_t fsmblks;  /* always 0 */
        size_t uordblks; /* total allocated space */
        size_t fordblks; /* total free space */
        size_t keepcost; /* releasable (via malloc_trim) space */
    };
    struct mallinfo mspace_mallinfo(mspace msp);
}

namespace fireball::coos {

void co_mem::init() {
    // dlmalloc initialization is typically lazy or handled by the first call.
    // If we need global setup, it goes here.
}

mspace_t co_mem::create_mspace(size_t capacity) {
    // locked=0 because we use cooperative multitasking (single threaded)
    return create_mspace(capacity, 0);
}

size_t co_mem::destroy_mspace(mspace_t msp) {
    if (!msp) return 0;
    return destroy_mspace(static_cast<mspace>(msp));
}

void* co_mem::alloc(mspace_t msp, size_t size) {
    if (!msp) return nullptr;
    return mspace_malloc(static_cast<mspace>(msp), size);
}

void co_mem::free(mspace_t msp, void* ptr) {
    if (!msp || !ptr) return;
    mspace_free(static_cast<mspace>(msp), ptr);
}

void* co_mem::realloc(mspace_t msp, void* ptr, size_t new_size) {
    if (!msp) return nullptr;
    return mspace_realloc(static_cast<mspace>(msp), ptr, new_size);
}

void co_mem::get_stats(mspace_t msp, size_t* allocated_out, size_t* capacity_out) {
    if (!msp) {
        if (allocated_out) *allocated_out = 0;
        if (capacity_out) *capacity_out = 0;
        return;
    }

    struct mallinfo info = mspace_mallinfo(static_cast<mspace>(msp));
    
    if (allocated_out) {
        *allocated_out = info.uordblks;
    }
    if (capacity_out) {
        // Total footprint = allocated + free
        *capacity_out = info.uordblks + info.fordblks;
    }
}

} // namespace fireball::coos
