#include "PluginProcessor.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <new>
#include <time.h>
#include <vector>

namespace allocation_probe
{
thread_local bool tracking = false;
thread_local std::uint64_t count = 0;

static void record() noexcept
{
    if (tracking)
        ++count;
}
}

void* operator new(std::size_t size)
{
    if (auto* memory = std::malloc(size))
    {
        allocation_probe::record();
        return memory;
    }
    throw std::bad_alloc();
}

void* operator new[](std::size_t size)
{
    return ::operator new(size);
}

void* operator new(std::size_t size, std::align_val_t alignment)
{
    void* memory = nullptr;
    if (
        posix_memalign(
            &memory,
            static_cast<std::size_t>(alignment),
            size) == 0
        && memory != nullptr
    )
    {
        allocation_probe::record();
        return memory;
    }
    throw std::bad_alloc();
}

void* operator new[](std::size_t size, std::align_val_t alignment)
{
    return ::operator new(size, alignment);
}

void operator delete(void* memory) noexcept
{
    std::free(memory);
}

void operator delete[](void* memory) noexcept
{
    std::free(memory);
}

void operator delete(void* memory, std::size_t) noexcept
{
    std::free(memory);
}

void operator delete[](void* memory, std::size_t) noexcept
{
    std::free(memory);
}

void operator delete(void* memory, std::align_val_t) noexcept
{
    std::free(memory);
}

void operator delete[](void* memory, std::align_val_t) noexcept
{
    std::free(memory);
}

void operator delete(
    void* memory,
    std::size_t,
    std::align_val_t) noexcept
{
    std::free(memory);
}

void operator delete[](
    void* memory,
    std::size_t,
    std::align_val_t) noexcept
{
    std::free(memory);
}

namespace
{
constexpr double sampleRate = 48000.0;
constexpr int blockSize = 128;
constexpr double tempo = 116.0;
constexpr double beatsPerBar = 4.0;
constexpr int warmupBars = 2;
constexpr int measuredBars = 16;
constexpr int simulatedTracks = 64;
constexpr int channelStripStages = 4;

class SyntheticPlayHead final : public juce::AudioPlayHead
{
public:
    juce::Optional<PositionInfo> getPosition() const override
    {
        return position;
    }

    void update(std::int64_t samplePosition, double ppqPosition)
    {
        PositionInfo next;
        next.setTimeInSamples(samplePosition);
        next.setTimeInSeconds(
            static_cast<double>(samplePosition) / sampleRate);
        next.setBpm(tempo);
        next.setPpqPosition(ppqPosition);
        next.setPpqPositionOfLastBarStart(
            std::floor(ppqPosition / beatsPerBar) * beatsPerBar);
        next.setTimeSignature(TimeSignature { 4, 4 });
        next.setIsPlaying(true);
        position = next;
    }

    void stop()
    {
        position.setIsPlaying(false);
    }

private:
    PositionInfo position;
};

class ProductionMixLoad
{
public:
    double process(const juce::AudioBuffer<float>& source) noexcept
    {
        auto blockSum = 0.0;
        for (int track = 0; track < simulatedTracks; ++track)
        {
            const auto* input = source.getReadPointer(track & 1);
            auto x = 0.0;
            const auto gain = 0.45 + 0.005 * static_cast<double>(track);

            for (int sample = 0; sample < blockSize; ++sample)
            {
                x = static_cast<double>(input[sample]) * gain;
                for (int stage = 0; stage < channelStripStages; ++stage)
                {
                    auto& state =
                        states[static_cast<std::size_t>(track)]
                              [static_cast<std::size_t>(stage)];
                    const auto coefficient =
                        0.025 + 0.005 * static_cast<double>(stage);
                    state += coefficient * (x - state);
                    x = state - 0.04 * state * state * state;
                }
                blockSum += x;
            }
        }

        checksum += blockSum;
        return blockSum;
    }

    double getChecksum() const noexcept
    {
        return checksum;
    }

private:
    std::array<
        std::array<double, channelStripStages>,
        simulatedTracks> states {};
    double checksum = 0.0;
};

std::uint64_t threadCpuTimeNs()
{
    timespec value {};
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &value) != 0)
        return 0;
    return (
        static_cast<std::uint64_t>(value.tv_sec) * 1000000000ULL
        + static_cast<std::uint64_t>(value.tv_nsec)
    );
}

std::uint64_t percentile(
    const std::vector<std::uint64_t>& values,
    double quantile)
{
    if (values.empty())
        return 0;

    auto sorted = values;
    std::sort(sorted.begin(), sorted.end());
    const auto index = static_cast<std::size_t>(
        std::floor(
            quantile
            * static_cast<double>(sorted.size() - 1)
        )
    );
    return sorted[index];
}

bool require(bool condition, const char* message)
{
    if (! condition)
        std::cerr << "FAIL: " << message << '\n';
    return condition;
}
}

int main()
{
    if (threadCpuTimeNs() == 0)
    {
        std::cerr << "FAIL: thread CPU clock is unavailable\n";
        return 1;
    }

    const auto blockDurationNs = static_cast<std::uint64_t>(
        1000000000.0 * static_cast<double>(blockSize) / sampleRate);
    const auto beatsPerBlock =
        (tempo / 60.0) * (static_cast<double>(blockSize) / sampleRate);
    const auto totalBars = warmupBars + measuredBars;
    const auto totalBlocks = static_cast<int>(
        std::ceil(
            static_cast<double>(totalBars) * beatsPerBar / beatsPerBlock
        )
    );

    session_player::SessionPlayerListenerAudioProcessor processor(false);
    SyntheticPlayHead playHead;
    juce::AudioBuffer<float> audio(2, blockSize);
    juce::MidiBuffer midi;
    ProductionMixLoad productionMix;

    processor.setPlayConfigDetails(
        2,
        2,
        sampleRate,
        blockSize);
    processor.setPlayHead(&playHead);
    processor.prepareToPlay(sampleRate, blockSize);

    for (int channel = 0; channel < audio.getNumChannels(); ++channel)
    {
        auto* samples = audio.getWritePointer(channel);
        for (int sample = 0; sample < blockSize; ++sample)
        {
            const auto phase =
                2.0 * juce::MathConstants<double>::pi
                * (110.0 + 55.0 * static_cast<double>(channel))
                * static_cast<double>(sample)
                / sampleRate;
            samples[sample] = static_cast<float>(0.2 * std::sin(phase));
        }
    }

    const auto warmupBlocks = static_cast<int>(
        std::ceil(
            static_cast<double>(warmupBars) * beatsPerBar / beatsPerBlock
        )
    );
    std::vector<std::uint64_t> callbackDurations;
    std::vector<std::uint64_t> boundaryDurations;
    std::vector<std::uint64_t> hostCycleDurations;
    callbackDurations.reserve(
        static_cast<std::size_t>(totalBlocks - warmupBlocks));
    boundaryDurations.reserve(measuredBars + 1);
    hostCycleDurations.reserve(
        static_cast<std::size_t>(totalBlocks - warmupBlocks));

    auto previousBar = -1;
    auto measuredAllocations = std::uint64_t { 0 };

    for (int block = 0; block < totalBlocks; ++block)
    {
        const auto samplePosition =
            static_cast<std::int64_t>(block) * blockSize;
        const auto ppqPosition =
            static_cast<double>(block) * beatsPerBlock;
        const auto observedBar = static_cast<int>(
            std::floor(ppqPosition / beatsPerBar));
        playHead.update(samplePosition, ppqPosition);

        const auto hostStart = threadCpuTimeNs();
        productionMix.process(audio);

        const auto callbackStart = threadCpuTimeNs();
        const auto allocationsBefore = allocation_probe::count;
        allocation_probe::tracking = true;
        processor.processBlock(audio, midi);
        allocation_probe::tracking = false;
        const auto callbackEnd = threadCpuTimeNs();
        measuredAllocations += allocation_probe::count - allocationsBefore;
        const auto hostEnd = threadCpuTimeNs();

        if (block >= warmupBlocks)
        {
            const auto callbackDuration = callbackEnd - callbackStart;
            callbackDurations.push_back(callbackDuration);
            hostCycleDurations.push_back(hostEnd - hostStart);
            if (previousBar >= 0 && observedBar > previousBar)
                boundaryDurations.push_back(callbackDuration);
        }
        previousBar = observedBar;
    }

    playHead.stop();
    processor.processBlock(audio, midi);
    processor.setPlayHead(nullptr);
    processor.releaseResources();

    const auto callbackP99 = percentile(callbackDurations, 0.99);
    const auto callbackP999 = percentile(callbackDurations, 0.999);
    const auto boundaryMax = boundaryDurations.empty()
        ? 0
        : *std::max_element(
            boundaryDurations.begin(),
            boundaryDurations.end());
    const auto hostCycleP99 = percentile(hostCycleDurations, 0.99);
    const auto hostCycleMax = hostCycleDurations.empty()
        ? 0
        : *std::max_element(
            hostCycleDurations.begin(),
            hostCycleDurations.end());

    std::cout
        << "Listener real-time stress: "
        << simulatedTracks << " tracks, "
        << channelStripStages << " stages, "
        << callbackDurations.size() << " measured callbacks, "
        << boundaryDurations.size() << " bar boundaries\n"
        << "block deadline ns=" << blockDurationNs
        << ", callback p99=" << callbackP99
        << ", callback p99.9=" << callbackP999
        << ", boundary max=" << boundaryMax
        << ", host-cycle p99=" << hostCycleP99
        << ", host-cycle max=" << hostCycleMax
        << ", callback allocations=" << measuredAllocations
        << ", mix checksum=" << productionMix.getChecksum()
        << '\n';

    auto passed = true;
    passed &= require(
        measuredAllocations == 0,
        "processBlock performed a callback-thread heap allocation");
    passed &= require(
        boundaryDurations.size() >= measuredBars - 1,
        "the stress run did not exercise enough bar-boundary handoffs");
    passed &= require(
        callbackP99 < blockDurationNs / 10,
        "Listener callback p99 exceeded 10% of the block deadline");
    passed &= require(
        callbackP999 < blockDurationNs / 5,
        "Listener callback p99.9 exceeded 20% of the block deadline");
    passed &= require(
        boundaryMax < blockDurationNs / 4,
        "bar-boundary callback exceeded 25% of the block deadline");
    passed &= require(
        hostCycleP99 < blockDurationNs * 3 / 4,
        "64-track host-cycle p99 exceeded 75% of the block deadline");
    passed &= require(
        hostCycleMax < blockDurationNs,
        "64-track host cycle exceeded the block deadline");
    passed &= require(
        std::isfinite(productionMix.getChecksum()),
        "production-mix workload produced an invalid result");

    return passed ? 0 : 1;
}
