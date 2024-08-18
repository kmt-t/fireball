
#ifndef CORO_EXPORT_H
#define CORO_EXPORT_H

#ifdef CORO_STATIC_DEFINE
#  define CORO_EXPORT
#  define CORO_NO_EXPORT
#else
#  ifndef CORO_EXPORT
#    ifdef libcoro_EXPORTS
        /* We are building this library */
#      define CORO_EXPORT 
#    else
        /* We are using this library */
#      define CORO_EXPORT 
#    endif
#  endif

#  ifndef CORO_NO_EXPORT
#    define CORO_NO_EXPORT 
#  endif
#endif

#ifndef CORO_DEPRECATED
#  define CORO_DEPRECATED __declspec(deprecated)
#endif

#ifndef CORO_DEPRECATED_EXPORT
#  define CORO_DEPRECATED_EXPORT CORO_EXPORT CORO_DEPRECATED
#endif

#ifndef CORO_DEPRECATED_NO_EXPORT
#  define CORO_DEPRECATED_NO_EXPORT CORO_NO_EXPORT CORO_DEPRECATED
#endif

/* NOLINTNEXTLINE(readability-avoid-unconditional-preprocessor-if) */
#if 0 /* DEFINE_NO_DEPRECATED */
#  ifndef CORO_NO_DEPRECATED
#    define CORO_NO_DEPRECATED
#  endif
#endif

#endif /* CORO_EXPORT_H */
