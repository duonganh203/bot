export class AppError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly statusCode: number,
  ) {
    super(message);
    this.name = 'AppError';
  }
}

export class ConcurrentUpdateError extends AppError {
  constructor() {
    super('CONCURRENT_UPDATE', 'Portfolio changed during execution; refresh context and retry.', 409);
  }
}
