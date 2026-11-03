export const db = {
  query: async (sql: string, params: unknown[] = []): Promise<any[]> => [],
  save: async (row: unknown): Promise<void> => {},
};
