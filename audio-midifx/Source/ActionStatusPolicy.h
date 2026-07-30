#pragma once

namespace session_player::bass
{
constexpr bool isSuccessfulHttpStatus (int statusCode)
{
    return statusCode >= 200 && statusCode < 300;
}

constexpr bool shouldPreserveProducerActionStatus (
    int statusCode,
    bool responseStreamOpened)
{
    return (
        ! responseStreamOpened
        || ! isSuccessfulHttpStatus (statusCode)
    );
}
}
