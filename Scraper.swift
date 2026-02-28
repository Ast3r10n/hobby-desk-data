//
//  Scraper.swift
//  
//
//  Created by Andrea Sacerdoti on 20/02/26.
//

import Foundation

func normalizeID(_ value: Any) -> String? {
    if let s = value as? String { return s }
    if let n = value as? NSNumber { return n.stringValue }
    return nil
}

// Root path to start searching from (current working directory)
let rootPath = FileManager.default.currentDirectoryPath
let fm = FileManager.default

// Recursively enumerate all files under rootPath
let enumerator = fm.enumerator(atPath: rootPath)

// Accumulator for all objects found across JSON files
var combined: [[String: Any]] = []
var seenIDs = Set<String>()
var duplicateCount = 0

while let item = enumerator?.nextObject() as? String {
    // Only consider files that end with .json and exclude combined.json
    guard item.hasSuffix(".json"), item != "combined.json" else { continue }

    let fileURL = URL(fileURLWithPath: rootPath).appendingPathComponent(item)

    do {
        let data = try Data(contentsOf: fileURL)
        let json = try JSONSerialization.jsonObject(with: data, options: [])

        // We only combine JSON files that are arrays of objects
        if let array = json as? [[String: Any]] {
            for (index, obj) in array.enumerated() {
                if let idValue = obj["id"], let idString = normalizeID(idValue) {
                    if seenIDs.contains(idString) {
                        fputs("Duplicate id '\(idString)' found in \(item) at index \(index). Skipping duplicate.\n", stderr)
                        duplicateCount += 1
                        continue
                    } else {
                        seenIDs.insert(idString)
                    }
                }
                combined.append(obj)
            }
        } else if json as? [Any] != nil {
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
    print("Wrote \(combined.count) unique objects to \(outputURL.path) (skipped \(duplicateCount) duplicates)")
} catch {
    fputs("Failed to write combined.json: \(error)\n", stderr)
    exit(1)
}

