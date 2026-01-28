#ifndef FIREBALL_UTILS_ECONOMIC_FUNCTION_HXX
#define FIREBALL_UTILS_ECONOMIC_FUNCTION_HXX

#include <cstddef>
#include <type_traits>
#include <utility>

namespace fireball::utils {

/**
 * @brief 経済的な関数 (Economic Function)
 * 
 * ヒープを一切使わず、固定サイズバッファに呼び出し可能オブジェクトを格納する。
 * Capacityを超えるキャプチャを持つ場合は static_assert でコンパイルエラーとなる。
 * 
 * {Policy_Memory} {Static_Resolution}
 */
template <typename Signature, std::size_t Capacity = 16>
class economic_function;

template <typename R, typename... Args, std::size_t Capacity>
class economic_function<R(Args...), Capacity> {
public:
    using result_type = R;

    constexpr economic_function() noexcept : vtable_(nullptr) {}

    template <typename F>
    economic_function(F&& f) {
        using DecayedF = std::decay_t<F>;
        static_assert(sizeof(DecayedF) <= Capacity, "Callable size exceeds economic_function capacity.");
        static_assert(std::is_invocable_r_v<R, DecayedF, Args...>, "Callable is not invocable with specified signature.");

        new (&buffer_) DecayedF(std::forward<F>(f));

        static const vtable_t vtable = {
            .call = [](const void* buf, Args... args) -> R {
                return (*static_cast<const DecayedF*>(buf))(std::forward<Args>(args)...);
            },
            .destroy = [](void* buf) {
                static_cast<DecayedF*>(buf)->~DecayedF();
            }
        };
        vtable_ = &vtable;
    }

    ~economic_function() {
        if (vtable_) {
            vtable_->destroy(&buffer_);
        }
    }

    economic_function(const economic_function&) = delete;
    economic_function& operator=(const economic_function&) = delete;

    economic_function(economic_function&& other) noexcept : vtable_(other.vtable_) {
        if (other.vtable_) {
            // For now, we do a bitwise copy of the buffer.
            // This is safe for lambdas with simple captures (pointers, ints).
            // A more robust implementation would use a 'move' function in the vtable.
            for (std::size_t i = 0; i < Capacity; ++i) {
                buffer_[i] = other.buffer_[i];
            }
        }
        other.vtable_ = nullptr;
    }

    R operator()(Args... args) const {
        return vtable_->call(&buffer_, std::forward<Args>(args)...);
    }

    explicit operator bool() const noexcept {
        return vtable_ != nullptr;
    }

private:
    struct vtable_t {
        R (*call)(const void*, Args...);
        void (*destroy)(void*);
    };

    alignas(std::max_align_t) char buffer_[Capacity];
    const vtable_t* vtable_;
};

} // namespace fireball::utils

#endif // FIREBALL_UTILS_ECONOMIC_FUNCTION_HXX
