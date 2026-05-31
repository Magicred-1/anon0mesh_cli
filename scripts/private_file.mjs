import {
    closeSync,
    constants,
    fchmodSync,
    fstatSync,
    lstatSync,
    openSync,
    readFileSync,
} from "node:fs";

export function readPrivateTextFileSync(path) {
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
        return readFileSync(fd, "utf8");
    } finally {
        closeSync(fd);
    }
}
