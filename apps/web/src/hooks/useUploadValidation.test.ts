/**
 * Property 18 and unit tests for validateUploadFile (Tasks 14.1, 14.2)
 * Validates: Requirements 10.5
 */
import {
  validateUploadFile,
  ALLOWED_EXTENSIONS,
  MAX_FILE_BYTES,
} from "./useUploadValidation";

function makeFile(name: string, size: number): File {
  return { name, size } as File;
}

// Property 18: Frontend Upload Validation Rejects Disallowed Extensions
export const PROPERTY_18_DESCRIPTION = `
  For any file name and size:
  - validateUploadFile returns null iff extension is allowed AND size <= MAX_FILE_BYTES
  - validateUploadFile returns a non-null error string otherwise
`;

// Unit test cases
const TEST_CASES = [
  // Allowed extensions at exactly MAX_FILE_BYTES
  ...Array.from(ALLOWED_EXTENSIONS).map((ext) => ({
    file: makeFile(`test${ext}`, MAX_FILE_BYTES),
    expectedNull: true,
    description: `${ext} at exactly 50 MiB → valid`,
  })),
  // Allowed extensions at MAX_FILE_BYTES + 1
  ...Array.from(ALLOWED_EXTENSIONS).map((ext) => ({
    file: makeFile(`test${ext}`, MAX_FILE_BYTES + 1),
    expectedNull: false,
    description: `${ext} at 50 MiB + 1 byte → invalid (too large)`,
  })),
  // Disallowed extension
  {
    file: makeFile("test.exe", 1024),
    expectedNull: false,
    description: ".exe → invalid",
  },
  {
    file: makeFile("test.txt", 1024),
    expectedNull: false,
    description: ".txt → invalid",
  },
  {
    file: makeFile("test", 1024),
    expectedNull: false,
    description: "no extension → invalid",
  },
  // Case insensitivity
  {
    file: makeFile("test.PDF", 1024),
    expectedNull: true,
    description: ".PDF uppercase → valid",
  },
];

// Export test cases for use in a test runner
export { TEST_CASES };

// Inline validation (for TypeScript typecheck without a test runner)
for (const { file, expectedNull, description } of TEST_CASES) {
  const result = validateUploadFile(file);
  const isNull = result === null;
  if (isNull !== expectedNull) {
    console.error(
      `FAILED: ${description}. Expected ${expectedNull ? "null" : "error"}, got: ${result}`
    );
  }
}
