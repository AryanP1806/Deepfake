import { NewInvestigation } from '../components/dashboard/NewInvestigation'

/** The full "new investigation" form with every option open. */
export default function Investigation({ copies = false }: { copies?: boolean }) {
  return (
    <div className="mx-auto max-w-[1100px]">
      <NewInvestigation expanded={!copies} copiesFocus={copies} />
    </div>
  )
}
