export function createStoryboardRevisionSync({ load, put }) {
  const revisions = new Map();
  return {
    async load(id) {
      const result = await load(id);
      revisions.set(id, result.revision);
      return result.blocks;
    },
    revisionOf: (id) => revisions.get(id) ?? null,
    async save(id, blocks, options) {
      if (!revisions.has(id)) await this.load(id);
      try {
        const result = await put(id, blocks, revisions.get(id), options);
        revisions.set(id, result.revision);
        return result;
      } catch (error) {
        if (error?.status === 409 && error.code === 'revision_conflict') {
          revisions.set(id, error.meta.revision);
        }
        throw error;
      }
    },
  };
}
