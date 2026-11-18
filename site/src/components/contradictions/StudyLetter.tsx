import styles from "./Contradictions.module.css";

/** The classifier's name for a study, which its reason lines use: A on the left, B on the right. */
export function StudyLetter({ letter }: { letter: "A" | "B" }) {
  return (
    <span className={styles.letter}>
      <span className={styles.visuallyHidden}>Study </span>
      {letter}
    </span>
  );
}
