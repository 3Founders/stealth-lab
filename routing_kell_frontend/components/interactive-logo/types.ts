/** Sample points derived from the real keळ mark, in normalized [0,1] source-image space. */
export interface LogoGeometry {
  nx: Float32Array;
  ny: Float32Array;
  /** 1 = boundary/silhouette sample, 0 = interior fill sample. */
  isContour: Uint8Array;
  /** 1 = sampled from the mark's yellow region, 0 = ink. */
  colorClass: Uint8Array;
  count: number;
}
