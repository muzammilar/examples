package orders

type Order struct {
	Phase            string `json:"phase"`
	ID               int64  `json:"order_id"`
	Customer         int64  `json:"customer_id"`
	Amount           int64  `json:"amount_cents"`
	Region           string `json:"region"`
	Revision         int64  `json:"order_revision"`
	CustomerRevision int64  `json:"customer_revision"`
	EventSecond      int64  `json:"event_second"`
}
