const notices = new Map();
export const setStoryboardEntryNotice = (projectId, notice) => notices.set(projectId, notice);
export const storyboardEntryNotice = projectId => notices.get(projectId) || null;
