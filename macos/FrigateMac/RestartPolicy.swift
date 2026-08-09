import Foundation

struct RestartPolicy: Equatable, Sendable {
    let maximumRestarts: Int
    let window: TimeInterval
    let delays: [TimeInterval]
    private(set) var attempts: [Date] = []

    init(
        maximumRestarts: Int = 3,
        window: TimeInterval = 300,
        delays: [TimeInterval] = [1, 2, 4]
    ) {
        self.maximumRestarts = maximumRestarts
        self.window = window
        self.delays = delays
    }

    mutating func nextDelay(at date: Date = Date()) -> TimeInterval? {
        attempts.removeAll { date.timeIntervalSince($0) >= window }
        guard attempts.count < maximumRestarts else { return nil }
        let delay = delays[min(attempts.count, max(delays.count - 1, 0))]
        attempts.append(date)
        return delay
    }

    mutating func reset() {
        attempts.removeAll()
    }
}
