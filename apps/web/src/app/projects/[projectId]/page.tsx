import { ProjectHistoryWorkspace } from "@/components/workspace/ProjectHistoryWorkspace";


export default async function ProjectPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <ProjectHistoryWorkspace projectId={projectId} />;
}
