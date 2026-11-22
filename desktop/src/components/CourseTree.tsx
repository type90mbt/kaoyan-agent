import type { Course, Lesson, Subject } from "../api/client";

type Props = {
  tree: Subject[];
  selectedCourseId: string | null;
  selectedLessonId: string | null;
  selectedMaterialId: string | null;
  busy?: boolean;
  onSelectCourse: (course: Course) => void;
  onSelectLesson: (course: Course, lesson: Lesson) => void;
  onSelectMaterial: (course: Course, materialId: string) => void;
  onCreateSubject: () => void;
  onCreateCourse: (subjectId: string) => void;
  onCreateLesson: (courseId: string) => void;
  onUpload: (courseId: string, file: File) => void;
};

export function CourseTree(props: Props) {
  return (
    <aside className="flex h-full flex-col border-r border-ink-200/80 bg-white/70 backdrop-blur">
      <div className="flex items-center justify-between border-b border-ink-100 px-4 py-3">
        <div>
          <div className="text-sm font-semibold tracking-wide text-ink-800">课程</div>
          <div className="text-xs text-ink-700/60">学科 · 课程 · 课件</div>
        </div>
        <button
          type="button"
          className="rounded-md bg-accent px-2.5 py-1 text-xs text-white hover:bg-accent/90 disabled:opacity-50"
          onClick={props.onCreateSubject}
          disabled={props.busy}
        >
          新建学科
        </button>
      </div>
      <div className="flex-1 overflow-auto p-2">
        {props.tree.length === 0 ? (
          <div className="rounded-lg border border-dashed border-ink-200 p-4 text-sm text-ink-700/70">
            还没有课程。先新建一个学科，再添加本学期的课。
          </div>
        ) : (
          <ul className="space-y-2">
            {props.tree.map((subject) => (
              <li key={subject.id} className="rounded-lg bg-ink-50/80 p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <div className="truncate text-sm font-medium text-ink-800">{subject.name}</div>
                  <button
                    type="button"
                    className="shrink-0 text-xs text-accent hover:underline"
                    onClick={() => props.onCreateCourse(subject.id)}
                    disabled={props.busy}
                  >
                    + 课程
                  </button>
                </div>
                <ul className="space-y-1 pl-1">
                  {subject.courses.map((course) => {
                    const active = props.selectedCourseId === course.id;
                    return (
                      <li key={course.id} className="rounded-md">
                        <button
                          type="button"
                          className={`w-full rounded-md px-2 py-1.5 text-left text-sm ${
                            active ? "bg-accent-soft text-accent" : "hover:bg-white"
                          }`}
                          onClick={() => props.onSelectCourse(course)}
                        >
                          {course.name}
                        </button>
                        {active && (
                          <div className="ml-2 mt-1 space-y-2 border-l border-ink-200 pl-2">
                            <div className="flex flex-wrap gap-2">
                              <button
                                type="button"
                                className="text-xs text-ink-700/70 hover:text-accent"
                                onClick={() => props.onCreateLesson(course.id)}
                              >
                                + 课时
                              </button>
                              <label className="cursor-pointer text-xs text-ink-700/70 hover:text-accent">
                                上传课件
                                <input
                                  type="file"
                                  className="hidden"
                                  accept=".pdf,.ppt,.pptx,.doc,.docx,.txt,.md"
                                  onChange={(e) => {
                                    const file = e.target.files?.[0];
                                    if (file) props.onUpload(course.id, file);
                                    e.currentTarget.value = "";
                                  }}
                                />
                              </label>
                            </div>
                            {course.lessons.length > 0 && (
                              <ul className="space-y-0.5">
                                {course.lessons.map((lesson) => (
                                  <li key={lesson.id}>
                                    <button
                                      type="button"
                                      className={`w-full rounded px-1.5 py-1 text-left text-xs ${
                                        props.selectedLessonId === lesson.id
                                          ? "bg-white text-accent"
                                          : "text-ink-700/80 hover:bg-white"
                                      }`}
                                      onClick={() => props.onSelectLesson(course, lesson)}
                                    >
                                      {lesson.title}
                                    </button>
                                  </li>
                                ))}
                              </ul>
                            )}
                            {course.materials.length > 0 && (
                              <ul className="space-y-0.5">
                                {course.materials.map((m) => (
                                  <li key={m.id}>
                                    <button
                                      type="button"
                                      className={`w-full truncate rounded px-1.5 py-1 text-left text-xs ${
                                        props.selectedMaterialId === m.id
                                          ? "bg-white text-accent"
                                          : "text-ink-700/80 hover:bg-white"
                                      }`}
                                      onClick={() => props.onSelectMaterial(course, m.id)}
                                      title={m.filename}
                                    >
                                      {m.filename}
                                      <span className="ml-1 text-[10px] opacity-60">
                                        {m.status}
                                        {m.pages ? ` · ${m.pages}页` : ""}
                                      </span>
                                    </button>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
