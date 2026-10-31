import fs from 'node:fs';
import path from 'node:path';

export function csvLine(values) {
  return values
    .map(v => {
      if (v === null || v === undefined) return '';
      const s = String(v);
      return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    })
    .join(',');
}

// Ghi CSV UTF-8 có BOM để Excel mở đúng tiếng Việt; ghi theo lô để chạy được cỡ lớn.
export class CsvWriter {
  constructor(file, header) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    this.file = file;
    this.fd = fs.openSync(file, 'w');
    this.buffer = [];
    this.bufferSize = 0;
    this.rows = 0;
    fs.writeSync(this.fd, '﻿' + csvLine(header) + '\n');
  }

  write(values) {
    this.writeLine(csvLine(values));
  }

  writeLine(line) {
    this.buffer.push(line);
    this.bufferSize += line.length;
    this.rows++;
    if (this.bufferSize > 1 << 20) this.flush();
  }

  flush() {
    if (!this.buffer.length) return;
    fs.writeSync(this.fd, this.buffer.join('\n') + '\n');
    this.buffer = [];
    this.bufferSize = 0;
  }

  close() {
    this.flush();
    fs.closeSync(this.fd);
    return this.rows;
  }
}

export function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n');
}
