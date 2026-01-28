# Concept: Economic Function (Heap-less type erasure)

class EconomicFunction:
    def __init__(self, capacity):
        self.capacity = capacity
        self.callable = None
        self.callable_size = 0

    def assign(self, callable_obj, size):
        # Implementation of static_assert at compile time in C++
        if size > self.capacity:
            raise Exception(f"Compile-time Error: size {size} exceeds capacity {self.capacity}")
        
        self.callable = callable_obj
        self.callable_size = size
        print(f"Assigned callable of size {size} to buffer of capacity {self.capacity}")

    def __call__(self, *args, **kwargs):
        if self.callable:
            return self.callable(*args, **kwargs)
        raise Exception("Callable is null")

# Usage Example
def test_concept():
    # Capacity for stateful callbacks
    func = EconomicFunction(capacity=16)

    # 1. Capture-less or small capture (Fits)
    small_state = [1, 2, 3] # simulate state
    def small_lambda():
        return sum(small_state)
    
    func.assign(small_lambda, size=8)
    print(f"Result: {func()}")

    # 2. Large capture (Exceeds capacity)
    large_state = list(range(100)) # simulate large capture
    def large_lambda():
        return sum(large_state)

    try:
        func.assign(large_lambda, size=32)
    except Exception as e:
        print(e) # This would be a compile error in C++

test_concept()
