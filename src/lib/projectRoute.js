export function shouldAdoptRouteProject(currentProjectId, routeProjectId) {
  return !!routeProjectId && currentProjectId !== routeProjectId;
}
