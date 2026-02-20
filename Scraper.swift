//
//  Scraper.swift
//  
//
//  Created by Andrea Sacerdoti on 20/02/26.
//

import Foundation

// Root path to start searching from (current working directory)
let rootPath = FileManager.default.currentDirectoryPath
let fm = FileManager.default

// Recursively enumerate all files under rootPath
let enumerator = fm.enumerator(atPath: rootPath)

// Accumulator for all objects found across JSON files
var combined: [[String: Any]] = []

while let item = enumerator?.nextObject() as? String {
    // Only consider files that end with .json
    guard item.hasSuffix(".json") else { continue }

    let fileURL = URL(fileURLWithPath: rootPath).appendingPathComponent(item)

    do {
        let data = try Data(contentsOf: fileURL)
        let json = try JSONSerialization.jsonObject(with: data, options: [])

        // We only combine JSON files that are arrays of objects
        if let array = json as? [[String: Any]] {
            combined.append(contentsOf: array)
        } else if let array = json as? [Any] {
            // If it's an array of non-dictionaries, still include them as-is by wrapping as Any
            // Convert [Any] to [[String: Any]] is not possible safely; skip with a warning
            fputs("Warning: Skipping \(item) because it isn't an array of objects.\n", stderr)
        } else {
            fputs("Warning: Skipping \(item) because it isn't a JSON array.\n", stderr)
        }
    } catch {
        fputs("Failed to process \(item): \(error)\n", stderr)
    }
}

// Write the combined array to combined.json in the root path
let outputURL = URL(fileURLWithPath: rootPath).appendingPathComponent("combined.json")

do {
    let jsonData = try JSONSerialization.data(withJSONObject: combined, options: [.prettyPrinted, .sortedKeys])
    try jsonData.write(to: outputURL, options: .atomic)
    print("Wrote \(combined.count) objects to \(outputURL.path)")
} catch {
    fputs("Failed to write combined.json: \(error)\n", stderr)
    exit(1)
}
