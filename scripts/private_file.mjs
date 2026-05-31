import {
    closeSync,
    constants,
    fchmodSync,
    fstatSync,
    lstatSync,
    openSync,
    readSync,
} from "node:fs";

const MAX_PRIVATE_FILE_BYTES = 1024 * 1024;

export function readPrivateTextFileSync(path, maxBytes = MAX_PRIVATE_FILE_BYTES) {
    const initial = lstatSync(path);
    if (initial.isSymbolicLink() || !initial.isFile()) {
        throw new Error("Refusing unsafe private file");
    }

    const flags = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0);
    const fd = openSync(path, flags);
    try {
        if (!fstatSync(fd).isFile()) {
            throw new Error("Refusing unsafe private file");
        }
        fchmodSync(fd, 0o600);
        const chunks = [];
        let total = 0;
        while (true) {
            const chunk = Buffer.alloc(Math.min(64 * 1024, maxBytes + 1 - total));
            const count = readSync(fd, chunk, 0, chunk.length, null);
            if (count === 0) break;
            total += count;
            if (total > maxBytes) {
                throw new Error("Private file exceeds size limit");
            }
            chunks.push(chunk.subarray(0, count));
        }
        return new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks));
    } finally {
        closeSync(fd);
    }
}
