#include "ActionStatusPolicy.h"

#include <array>
#include <iostream>

namespace
{
struct StatusCase
{
    int statusCode;
    bool responseStreamOpened;
    bool shouldPreserve;
};
}

int main()
{
    constexpr std::array<StatusCase, 7> cases {{
        { 200, true, false },
        { 204, true, false },
        { 409, true, true },
        { 422, true, true },
        { 503, true, true },
        { 0, false, true },
        { 200, false, true },
    }};

    for (const auto& test : cases)
    {
        const auto actual =
            session_player::bass::shouldPreserveProducerActionStatus (
                test.statusCode,
                test.responseStreamOpened);
        if (actual != test.shouldPreserve)
        {
            std::cerr
                << "Unexpected producer-action status policy for HTTP "
                << test.statusCode
                << ", stream=" << test.responseStreamOpened
                << ": expected " << test.shouldPreserve
                << ", got " << actual << '\n';
            return 1;
        }
    }

    return 0;
}
