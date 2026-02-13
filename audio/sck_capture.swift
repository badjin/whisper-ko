// sck_capture.swift — ScreenCaptureKit 시스템 오디오 캡처 CLI
//
// 시스템 오디오를 캡처하여 raw PCM (Int16, mono) 데이터를 stdout으로 출력한다.
// Python subprocess에서 stdout.read()로 프레임을 읽어 무음 감지/청크 분할한다.
//
// 사용법: sck_capture [--sample-rate 16000] [--channels 1]
// 종료:  SIGTERM 또는 SIGINT
// 종료 코드: 0=정상, 1=권한거부, 2=캡처실패
//
// 컴파일: swiftc -O -o audio/sck_capture audio/sck_capture.swift \
//          -framework ScreenCaptureKit -framework CoreMedia \
//          -framework AVFoundation -framework Foundation

import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

// ── 전역 상태 ─────────────────────────────────────────────
var isRunning = true

// ── CLI 인자 파싱 ─────────────────────────────────────────
func parseArgs() -> (sampleRate: Int, channels: Int) {
    var sampleRate = 16000
    var channels = 1
    let args = CommandLine.arguments

    var i = 1
    while i < args.count {
        switch args[i] {
        case "--sample-rate":
            if i + 1 < args.count, let v = Int(args[i + 1]) {
                sampleRate = v
                i += 1
            }
        case "--channels":
            if i + 1 < args.count, let v = Int(args[i + 1]) {
                channels = v
                i += 1
            }
        default:
            break
        }
        i += 1
    }
    return (sampleRate, channels)
}

// ── 오디오 출력 델리게이트 ─────────────────────────────────
class AudioOutputHandler: NSObject, SCStreamOutput {
    let targetSampleRate: Int
    let targetChannels: Int
    private var formatLogged = false

    init(sampleRate: Int, channels: Int) {
        self.targetSampleRate = sampleRate
        self.targetChannels = channels
        super.init()
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .audio, isRunning else { return }
        guard sampleBuffer.isValid else { return }

        // CMSampleBuffer → PCM 데이터 추출
        guard let blockBuffer = sampleBuffer.dataBuffer else { return }

        var length = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        let status = CMBlockBufferGetDataPointer(
            blockBuffer, atOffset: 0, lengthAtOffsetOut: nil,
            totalLengthOut: &length, dataPointerOut: &dataPointer
        )
        guard status == kCMBlockBufferNoErr, let ptr = dataPointer, length > 0 else {
            return
        }

        // 포맷 정보 확인
        guard let formatDesc = sampleBuffer.formatDescription else { return }
        guard let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(formatDesc)?.pointee
        else { return }

        let srcSampleRate = Int(asbd.mSampleRate)
        let srcChannels = Int(asbd.mChannelsPerFrame)
        let bytesPerFrame = Int(asbd.mBytesPerFrame)
        let isNonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0

        if !formatLogged {
            formatLogged = true
            fputs("INFO: Audio format: rate=\(srcSampleRate) ch=\(srcChannels) nonInterleaved=\(isNonInterleaved)\n", stderr)
        }

        guard bytesPerFrame > 0, srcChannels > 0 else { return }

        // non-interleaved 오디오 처리
        // ScreenCaptureKit은 non-interleaved(planar) Float32를 전달할 수 있음
        // 이 경우 blockBuffer에는 모든 채널 데이터가 순차적으로 저장됨
        let framesPerChannel: Int
        if isNonInterleaved {
            // non-interleaved: bytesPerFrame = 한 채널의 한 프레임 크기 (4 for Float32)
            // 전체 length = bytesPerFrame * framesPerChannel * srcChannels
            framesPerChannel = length / (Int(asbd.mBitsPerChannel / 8) * srcChannels)
        } else {
            framesPerChannel = length / bytesPerFrame
        }

        // Float32 데이터 접근
        let totalFloats = length / MemoryLayout<Float32>.size
        let floatPtr = UnsafeRawPointer(ptr).bindMemory(
            to: Float32.self, capacity: totalFloats
        )

        // 모노 다운믹스
        var monoSamples: [Float32]
        if srcChannels == 1 {
            monoSamples = Array(UnsafeBufferPointer(start: floatPtr, count: framesPerChannel))
        } else if isNonInterleaved {
            // planar: [ch0_frame0, ch0_frame1, ..., ch1_frame0, ch1_frame1, ...]
            monoSamples = [Float32](repeating: 0, count: framesPerChannel)
            for i in 0..<framesPerChannel {
                var sum: Float32 = 0
                for ch in 0..<srcChannels {
                    sum += floatPtr[ch * framesPerChannel + i]
                }
                monoSamples[i] = sum / Float32(srcChannels)
            }
        } else {
            // interleaved: [ch0_f0, ch1_f0, ch0_f1, ch1_f1, ...]
            monoSamples = [Float32](repeating: 0, count: framesPerChannel)
            for i in 0..<framesPerChannel {
                var sum: Float32 = 0
                for ch in 0..<srcChannels {
                    sum += floatPtr[i * srcChannels + ch]
                }
                monoSamples[i] = sum / Float32(srcChannels)
            }
        }

        // 리샘플링 (필요 시 — 단순 선형 보간)
        var outputSamples: [Float32]
        if srcSampleRate != targetSampleRate {
            let ratio = Double(targetSampleRate) / Double(srcSampleRate)
            let outCount = Int(Double(monoSamples.count) * ratio)
            outputSamples = [Float32](repeating: 0, count: outCount)
            for i in 0..<outCount {
                let srcIdx = Double(i) / ratio
                let idx0 = Int(srcIdx)
                let frac = Float32(srcIdx - Double(idx0))
                let s0 = idx0 < monoSamples.count ? monoSamples[idx0] : 0
                let s1 = (idx0 + 1) < monoSamples.count ? monoSamples[idx0 + 1] : s0
                outputSamples[i] = s0 + frac * (s1 - s0)
            }
        } else {
            outputSamples = monoSamples
        }

        // Float32 → Int16 변환
        var int16Samples = [Int16](repeating: 0, count: outputSamples.count)
        for i in 0..<outputSamples.count {
            let clamped = max(-1.0, min(1.0, outputSamples[i]))
            int16Samples[i] = Int16(clamped * 32767.0)
        }

        // stdout으로 출력
        int16Samples.withUnsafeBufferPointer { buf in
            let rawPtr = UnsafeRawPointer(buf.baseAddress!)
            let byteCount = buf.count * MemoryLayout<Int16>.size
            let written = fwrite(rawPtr, 1, byteCount, stdout)
            if written < byteCount {
                // stdout 닫힘 (Python 프로세스 종료)
                isRunning = false
            }
        }
    }
}

// ── 메인 ──────────────────────────────────────────────────
func main() async {
    let config = parseArgs()

    // stdout unbuffered
    setbuf(stdout, nil)

    // SIGTERM/SIGINT 핸들링
    let sigSources = [
        DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main),
        DispatchSource.makeSignalSource(signal: SIGINT, queue: .main),
    ]
    signal(SIGTERM, SIG_IGN)
    signal(SIGINT, SIG_IGN)
    for src in sigSources {
        src.setEventHandler { isRunning = false }
        src.resume()
    }

    // ScreenCaptureKit 권한 확인 및 콘텐츠 가져오기
    let content: SCShareableContent
    do {
        content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false
        )
    } catch {
        let nsErr = error as NSError
        // 권한 거부: TCC 에러 또는 SCStreamError
        if nsErr.code == -3801 || nsErr.domain == "com.apple.ScreenCaptureKit.SCStreamErrorDomain"
        {
            fputs("ERROR: Screen Recording permission denied\n", stderr)
            exit(1)
        }
        fputs("ERROR: Failed to get shareable content: \(error)\n", stderr)
        exit(2)
    }

    // 디스플레이 필터 (오디오 전용이지만 최소 1개 디스플레이 필요)
    guard let display = content.displays.first else {
        fputs("ERROR: No display found\n", stderr)
        exit(2)
    }

    let filter = SCContentFilter(display: display, excludingWindows: [])

    // 스트림 설정 — 오디오만 캡처
    let streamConfig = SCStreamConfiguration()
    streamConfig.capturesAudio = true
    streamConfig.excludesCurrentProcessAudio = false
    streamConfig.sampleRate = config.sampleRate
    streamConfig.channelCount = config.channels

    // 비디오 비활성화 (최소 크기로 설정)
    streamConfig.width = 2
    streamConfig.height = 2
    streamConfig.minimumFrameInterval = CMTime(value: 1, timescale: 1)  // 1fps 최소
    streamConfig.showsCursor = false

    // 스트림 생성 및 시작
    let stream: SCStream
    let handler = AudioOutputHandler(
        sampleRate: config.sampleRate, channels: config.channels
    )

    do {
        stream = SCStream(filter: filter, configuration: streamConfig, delegate: nil)
        try stream.addStreamOutput(handler, type: .audio, sampleHandlerQueue: .global())
        try await stream.startCapture()
    } catch {
        fputs("ERROR: Failed to start capture: \(error)\n", stderr)
        exit(2)
    }

    fputs("INFO: Audio capture started (rate=\(config.sampleRate), ch=\(config.channels))\n", stderr)

    // 메인 루프 — isRunning이 false가 될 때까지 대기
    while isRunning {
        try? await Task.sleep(nanoseconds: 100_000_000)  // 100ms
    }

    // 정리
    do {
        try await stream.stopCapture()
    } catch {
        // 이미 종료된 경우 무시
    }

    fputs("INFO: Audio capture stopped\n", stderr)
    exit(0)
}

// ── 진입점 ────────────────────────────────────────────────
if #available(macOS 13.0, *) {
    // async main 실행
    let semaphore = DispatchSemaphore(value: 0)
    Task {
        await main()
        semaphore.signal()
    }
    semaphore.wait()
} else {
    fputs("ERROR: macOS 13.0 or later required\n", stderr)
    exit(2)
}
